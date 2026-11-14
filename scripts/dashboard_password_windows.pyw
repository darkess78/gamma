"""User-operated Shana password setup. No plaintext credentials are saved or logged."""
import argparse
import base64
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import uuid

PROJECT = Path(__file__).resolve().parents[1]

class Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

def protect(data, *, decrypt=False):
    if sys.platform != 'win32':
        raise RuntimeError('Windows account encryption is required')
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    memory = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(memory, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    if decrypt:
        call = crypt.CryptUnprotectData
        call.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = call(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        call = crypt.CryptProtectData
        call.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = call(ctypes.byref(source), 'Shana dashboard login', None, None, None, 1, ctypes.byref(result))
    if not ok:
        raise OSError('Windows account encryption failed')
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel.LocalFree(result.pbData)

def record_for(username, password):
    if not isinstance(password, str) or len(password) < 12 or len(password.encode('utf-8')) > 1024:
        raise ValueError('Use at least twelve characters, up to 1024 UTF-8 bytes.')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=131072, r=8, p=1, dklen=32, maxmem=268435456)
    encoded = lambda value: base64.b64encode(value).decode('ascii')
    return {'version': 1, 'username': username, 'password_hash': 'scrypt-v1$' + encoded(salt) + '$' + encoded(digest),
            'session_secret': secrets.token_urlsafe(48), 'credential_version': uuid.uuid4().hex}

def atomic(path, data):
    temporary = path.with_name(path.name + '-' + uuid.uuid4().hex + '.tmp')
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

def load_vault(path):
    return json.loads(protect(Path(path).read_bytes(), decrypt=True))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--username', default='darkess78')
    parser.add_argument('--self-check', action='store_true')
    args = parser.parse_args()
    folder = args.output_dir.resolve()
    if not folder.is_relative_to((PROJECT/'data/coordination').resolve()):
        raise ValueError('Credential setup stays under this checkout private data')
    folder.mkdir(parents=True, exist_ok=True)
    if args.self_check:
        synthetic = b'synthetic-encryption-check'
        encrypted = protect(synthetic)
        assert encrypted != synthetic and protect(encrypted, decrypt=True) == synthetic
        atomic(folder/'self-check.json', b'{"windows_current_user_roundtrip":true,"plaintext_saved":false}')
        return
    import tkinter as tk
    root = tk.Tk(); root.title('Set your Shana dashboard password'); root.configure(bg='#15121f')
    root.resizable(False, False)
    body = tk.Frame(root, bg='#15121f', padx=28, pady=22); body.pack()
    fg='#f3efff'; accent='#78d7ed'
    def label(text):
        tk.Label(body, text=text, bg='#15121f', fg=fg, anchor='w', justify='left', wraplength=440).pack(fill='x', pady=(0,8))
    label('Your Shana dashboard login')
    label('Username: ' + args.username)
    label('Choose a password of at least 12 characters. Enter it here, not in chat.')
    tk.Label(body, text='Password', bg='#15121f', fg=fg, anchor='w').pack(fill='x')
    first = tk.Entry(body, show='*', width=42, font=('Segoe UI',12), bg='#211d2e', fg=fg, insertbackground=fg); first.pack(fill='x', pady=(3,12))
    tk.Label(body, text='Confirm password', bg='#15121f', fg=fg, anchor='w').pack(fill='x')
    second = tk.Entry(body, show='*', width=42, font=('Segoe UI',12), bg='#211d2e', fg=fg, insertbackground=fg); second.pack(fill='x', pady=(3,12))
    label('The login is encrypted for your Windows account. Gamma will store a salted password verifier, not the password itself.')
    status = tk.StringVar(value='No password has been saved.')
    tk.Label(body, textvariable=status, bg='#15121f', fg=accent, wraplength=440, justify='left').pack(fill='x',pady=8)
    outcome=[]
    def finish_poll():
        if not outcome:
            root.after(100,finish_poll); return
        ok, value = outcome.pop()
        if ok:
            first.configure(state='normal');second.configure(state='normal');first.delete(0,'end');second.delete(0,'end');first.configure(state='disabled');second.configure(state='disabled');status.set('Saved securely. You can close this window; deployment preparation will continue.')
            button.configure(text='Saved',state='disabled')
        else:
            first.configure(state='normal');second.configure(state='normal');button.configure(state='normal');status.set(value)
    def save():
        password=first.get()
        if password != second.get():
            status.set('The two passwords do not match.');return
        if len(password)<12 or len(password.encode('utf-8'))>1024:
            status.set('Use at least 12 characters, up to 1024 UTF-8 bytes.');return
        first.configure(state='disabled');second.configure(state='disabled');button.configure(state='disabled');status.set('Saving securely...')
        def worker():
            try:
                record=record_for(args.username,password)
                payload=json.dumps({'username':args.username,'password':password,'record':record}).encode('utf-8')
                encrypted=protect(payload)
                atomic(folder/'login.dpapi',encrypted)
                atomic(folder/'setup-status.json',json.dumps({'status':'saved','username':args.username,'saved_at':datetime.now(timezone.utc).isoformat(),'vault_file':str(folder/'login.dpapi'),'plaintext_saved':False}).encode())
                outcome.append((True,None))
            except Exception as exc:
                outcome.append((False,'Password setup could not finish: '+type(exc).__name__))
        threading.Thread(target=worker,daemon=True).start();root.after(100,finish_poll)
    button=tk.Button(body,text='Save my password',command=save,bg=accent,fg='#08202b',padx=16,pady=10,font=('Segoe UI',11,'bold'));button.pack(anchor='e',pady=(8,0))
    first.focus_set()
    atomic(folder/'setup-status.json',json.dumps({'status':'waiting_for_owner','username':args.username,'plaintext_saved':False}).encode())
    root.mainloop()

if __name__ == '__main__':
    main()
