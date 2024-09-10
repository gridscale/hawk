import gshawk.vars
import os
import base64
import yaml
import json
import sys
import ctypes
import platform
import subprocess
import re
from gshawk.vars import global_args
from gshawk.systemd import secrets as systemd
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.hmac import HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if platform.system() == 'Linux' and os.getenv('INSECURE_NO_LOCK') is None:
    # When we're on Linux, ensure we don't accidentally swap stuff.
    libc = ctypes.CDLL(None)
    libc.syscall(151, 3) # mlockall(MCL_CURRENT|MCL_FUTURE). Requires CAP_IPC_LOCK

class OnePassword:
    account = None
    __cache = {}
    def __init__(self):
        self.account = self.__getGSAccount()
    def __op(self, commands):
        sudo = os.getenv("SUDO_USER")
        op = ["op", "--format=json"] + commands
        if sudo is not None:
            op = ["sudo", "-u", sudo] + op
        payload = json.loads(subprocess.run(op, capture_output=True).stdout.decode('utf-8'))
        return payload
    def __getGSAccount(self):
        accounts = self.__op(["accounts", "list"])
        for acc in accounts:
            if acc['email'].endswith('@gridscale.io'):
                return acc
        return None
    def __listKeys(self):
        print("1Password: Listing 'hawk-crypt' tagged items in account '%s'" % self.account['email'], file=sys.stderr)
        key_items = self.__op(['item', 'list', '--tags', 'hawk-crypt'])
        for item in key_items:
            self.__getKey(item['id'])
            if self.__cache.get('.') is not None:
                break
    def __getKey(self, item_id):
        print("1Password: Reading item '%s'..." % item_id, file=sys.stderr)
        item = self.__op(['item', 'get', item_id])
        subtree = None
        key = None
        for field in item['fields']:
            if field['id'] == 'password':
                key = field['value']
            if field['id'] == 'username':
                subtree = field['value']
        if subtree is None or key is None:
            return False
        print("1Password: Identified key for tree '%s'" % subtree, file=sys.stderr)
        self.__cache[subtree] = key
        return True
    def populateKeyManager(self, km):
        self.__listKeys()
        for subtree in self.__cache:
            print("1Password: Handing over key for tree '%s' to hawk-crypt..." % subtree ,file=sys.stderr)
            km.setPathKey(subtree, self.__cache[subtree])

class InvalidKey(Exception):
    def __init__(self):            
        # Call the base class constructor with the parameters it needs
        super().__init__("Invalid key format. Must be <salt><key>. Each hex encoded, each 32bytes")
class NotFound(Exception):
    def __init__(self, wanted, have, failed):
        super().__init__('Secret \'%s\' is not defined. Could not find required field %s following \'%s\'.' % (subtree.escape(wanted), failed, subtree.escape('.'.join(have))))
class UnknownEncryption(Exception):
    def __init__(self):
        super().__init__('Unknown Encryption')
class UnencryptedContent(Exception):
    def __init__(self):
        super().__init__('Unencrypted Content')

class subtree:
    @staticmethod
    def escape(subtree_name):
        return '.' + re.sub(r'[^a-z0-9\._-]+', '', subtree_name.lower()).strip('.').replace('..', '.') # Guarantee a lowercase string with leading period and no trailing periods
    @staticmethod
    def parent(subtree_name):
        return os.path.dirname(subtree.escape(subtree_name).replace('.','/')).replace('/', '.')
    @staticmethod
    def key(subtree_name):
        return os.path.basename(subtree.escape(subtree_name).replace('.','/')).replace('/', '.')

class KeyManager:
    __key_cache = {}

    def __validatedKey(self,hex_key):
        if hex_key is not None:
            # Secret passed explicitly, disabling derived key
            raw = bytes.fromhex(hex_key)
            if len(raw) != 64:
                raise InvalidKey()
            return raw
        return InvalidKey()

    def __deriveSubKey(self, subtree_name):
        parent = subtree.parent(subtree_name)
        subtree_element = subtree.key(subtree_name)
        subtree_element_bytes = subtree_element.encode('utf-8')
        if parent == subtree_name:
            # We try to "derive" the root key, which is impossible
            return None

        try:
            bytes_key = self.pathKey(parent)
        except:
            return None
        parent_salt = bytes_key[:32]
        parent_key = bytes_key[32:]

        salt_hmac = HMAC(parent_salt, hashes.SHA256())
        salt_hmac.update(subtree_element_bytes)
        sub_salt = salt_hmac.finalize()

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=sub_salt,
            info=subtree_element_bytes,
        )
        sub_key = hkdf.derive(parent_key)
        out = sub_salt + sub_key
        return out

    def setPathKey(self, subtree_name, key_hex):
        subtree_name = subtree.escape(subtree_name)
        self.__key_cache[subtree_name] = self.__validatedKey(key_hex)

    def pathKey(self, subtree_name):
        subtree_name = subtree.escape(subtree_name)

        if subtree_name in self.__key_cache:
            return self.__key_cache[subtree_name]
       
        key = self.__deriveSubKey(subtree_name)
        if key is None and os.getenv('HAWK_ACCESS_KEY%s' % subtree_name.replace('.', '_')):
            key = self.__validatedKey(os.getenv('HAWK_ACCESS_KEY%s' % subtree_name.replace('.', '_')))
        if key is None and platform.system() == 'Linux':
            # Try systemd subkey if specified
            secret = systemd.read('hawkAccessKey%s' % subtree_name, check=False)
            key = None
            if secret is not None:
                key = self.__validatedKey(secret)
        if not key:
            raise Exception("decryption key for tree '%s' required but not provided." % subtree_name)
        self.__key_cache[subtree_name] = key
        return key

    def encrypt(self, subtree_name, json_data):
        subtree_name = subtree.escape(subtree_name)
        nonce = os.urandom(12) # 96-bit IV ass suggested by NIST
        key_full = self.pathKey(subtree_name)
        key = key_full[32:]

        aes = AESGCM(key)
        enc = aes.encrypt(nonce, json_data.encode('utf-8'), subtree_name.encode('utf-8'))
        return "ENC[hawkc00]" + base64.b64encode(nonce + enc).decode('utf-8')

    def decrypt(self, subtree_name, base64_data):
        if type(base64_data) is not str or base64_data[:8] != "ENC[hawk":
            raise UnencryptedContent()
        if base64_data[:12] != "ENC[hawkc00]":
            raise UnknownEncryption()
        subtree_name = subtree.escape(subtree_name)
        data = base64.b64decode(base64_data[12:])
        nonce = data[:12]
        key_full = self.pathKey(subtree_name)
        key = key_full[32:]

        aes = AESGCM(key)
        enc = aes.decrypt(nonce, data[12:], subtree_name.encode('utf-8'))
        return enc.decode('utf-8')

class Managed:
    __key_manager = None
    systemd = systemd
    encrypted = {}
    def __init__(self):
        self.__key_manager = KeyManager()
        yml_path = os.path.join(global_args['source'], "secrets.yml")
        try:
            with open(yml_path, 'r') as stream:
                self.encrypted = yaml.safe_load(stream)
        except:
            print('Warn: Unable to load encrypted secrets from \'%s\'' % yml_path, file=sys.stderr)

    def __find(self, element):
        keys = element.strip('.').split('.')
        read = []
        rv = self.encrypted
        failed = ""
        try:
            for key in keys:
                if key == '':
                    return rv
                rv = rv[key]
                read.append(key)
            return rv
        except Exception as e:
            if type(e) is not TypeError:
                pass
            failed = str(e)
        raise NotFound(element, read, failed)
    def derived_key(self, name):
        return self.__key_manager.pathKey(name).hex()
    def read(self, name, check=True):
        value = systemd.read(name, check=False)
        if value is not None:
            return value

        try:
            name = subtree.escape(name)
            data = self.__find(name)
            if type(data) is dict:
                err_name = '' if name == '.' else name
                raise Exception('Requested secret must be a leaf. Reading a secret dict is not supported. Available child values: %s.%s' % (err_name, (', %s.' % err_name).join(data.keys())))
        except Exception as e :
            if check:
                raise e
            print('Warn: Error while reading secret \'%s\': %s' % (name, str(e)), file=sys.stderr)
            return None
        return json.loads(self.__key_manager.decrypt(name, data))

