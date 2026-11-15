import ctypes
import datetime
import json
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .client import call
from .store import WardenError


def status(d):
    if d['lease_state'] == 'recovery_required' or (d['kind'] and d['connection'] != 'connected'):
        return '接続切れ・要確認' if d['connection'] != 'connected' else '要確認'
    if d['kind'] == 'human':
        return '自分が使用中'
    if d['kind'] == 'ai':
        return 'AI使用中'
    if d['connection'] == 'connected':
        return '管理内では未確保'
    if d['connection'] == 'disconnected':
        return '管理内では未確保（ADB未接続）'
    return '管理内では未確保（ADB要確認）'


def stamp(value):
    return datetime.datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M:%S') if value else '不明'


class App:
    def __init__(self, root):
        self.root = root
        root.title('Device Warden')
        root.geometry('1000x520')
        self.rows = []
        self.hidden = tk.BooleanVar(value=False)
        ttk.Label(root, text='「管理内では未確保」は空きの保証ではありません。直接ADB操作や物理的な利用は検知できません。', foreground='#a03319').pack(pady=6)
        self.message = ttk.Label(root, text='')
        self.message.pack()
        columns = ('name', 'connection', 'status', 'owner', 'policy', 'seen')
        self.tree = ttk.Treeview(root, columns=columns, show='headings', height=12)
        for key, title, width in [('name','端末',180),('connection','ADB',110),('status','利用可否',220),('owner','確保者',180),('policy','AI',80),('seen','最終確認',150)]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width)
        self.tree.pack(fill='both', expand=True, padx=8)
        bar = ttk.Frame(root)
        bar.pack(fill='x', pady=5)
        for label, fn in [('最新取得', self.refresh), ('自分が確保', self.claim), ('解放', self.release), ('強制解放', self.force), ('AI許可切替', self.policy), ('詳細', self.details)]:
            ttk.Button(bar, text=label, command=fn).pack(side='left')
        bar = ttk.Frame(root)
        bar.pack(fill='x', pady=5)
        for label, fn in [('再確認', self.reconfirm), ('scrcpy', self.scrcpy), ('名前変更', self.rename), ('表示切替', self.toggle_hidden), ('削除', self.delete), ('新規端末のAI既定', self.default_policy), ('履歴', self.audit), ('自動起動', self.autostart)]:
            ttk.Button(bar, text=label, command=fn).pack(side='left')
        ttk.Checkbutton(root, text='非表示端末も表示', variable=self.hidden, command=self.load).pack()
        root.protocol('WM_DELETE_WINDOW', root.withdraw)
        self.load()
        self.tick()

    def tick(self):
        self.load()
        self.root.after(10000, self.tick)

    def run(self, action, **args):
        try:
            result = call(action, **args)
            if action != 'list':
                self.message.config(text='')
            return result
        except WardenError as exc:
            self.message.config(text=str(exc))
            return None

    def load(self):
        rows = self.run('list', include_hidden=self.hidden.get())
        if rows is None:
            return
        selected = self.tree.selection()
        selected_id = selected[0] if selected else None
        self.tree.delete(*self.tree.get_children())
        self.rows = rows
        for d in rows:
            owner = (d['tool'] or 'AI') if d['kind'] == 'ai' else ('自分' if d['kind'] == 'human' else 'なし')
            name = d['display_name']
            if d['hidden']:
                name += '（非表示）'
            self.tree.insert('', 'end', iid=d['id'], values=(name, f"{d['connection']} / {d['transport']}", status(d), owner, '許可' if d['ai_allowed'] else '禁止', stamp(d['last_seen'])))
        if selected_id and self.tree.exists(selected_id):
            self.tree.selection_set(selected_id)
        if not rows:
            self.message.config(text='登録された端末はありません。ADBを接続して最新取得してください。')

    def selected(self):
        ids = self.tree.selection()
        if not ids:
            return None
        return next((d for d in self.rows if d['id'] == ids[0]), None)

    def require_selection(self):
        d = self.selected()
        if not d:
            messagebox.showinfo('端末', '端末を選択してください。')
        return d

    def refresh(self):
        try:
            call('refresh')
            self.load()
        except WardenError as exc:
            self.message.config(text=str(exc))

    def claim(self):
        d = self.require_selection()
        if d:
            self.run('claim', kind='human', device_id=d['id'])
            self.load()

    def release(self):
        d = self.require_selection()
        if d and d['kind'] == 'human':
            self.run('release', lease_id=d['lease_id'])
            self.load()
        elif d:
            messagebox.showinfo('解放', '自分の確保のみ通常解放できます。AIの確保は強制解放を使用してください。')

    def force(self):
        d = self.require_selection()
        if d and d['kind'] and messagebox.askyesno('強制解放', '作業中のAIや利用者の操作を中断し、競合を引き起こす可能性があります。強制解放しますか？'):
            self.run('release', force=True, device_id=d['id'])
            self.load()

    def reconfirm(self):
        d = self.require_selection()
        if not d:
            return
        result = self.run('reconfirm', device_id=d['id'])
        if result:
            self.load()
            messagebox.showinfo('再確認', f"ADB: {result['connection']}\n"
                                f"リース: {'要確認' if result['lease_state'] == 'recovery_required' else result['lease_state'] or 'なし'}\n\n"
                                '接続が戻ってもリースは自動解放されません。所有者を確認してから操作してください。')

    def scrcpy(self):
        d = self.require_selection()
        if d:
            self.run('scrcpy', device_id=d['id'])
            self.load()

    def policy(self):
        d = self.require_selection()
        if d:
            self.run('update', device_id=d['id'], ai_allowed=int(not d['ai_allowed']))
            self.load()

    def rename(self):
        d = self.require_selection()
        if d:
            name = simpledialog.askstring('名前変更', '別名（空欄で解除）', initialvalue=d['alias'] or '')
            if name is not None:
                self.run('update', device_id=d['id'], alias=name or None)
                self.load()

    def toggle_hidden(self):
        d = self.require_selection()
        if d:
            self.run('update', device_id=d['id'], hidden=int(not d['hidden']))
            self.load()

    def delete(self):
        d = self.require_selection()
        if d and messagebox.askyesno('削除', '端末の登録を削除しますか？再検出される場合があります。'):
            self.run('delete', device_id=d['id'])
            self.load()

    def audit(self):
        d = self.selected() if self.tree.selection() else None
        rows = self.run('audit', device_id=d['id'] if d else None)
        if not rows:
            messagebox.showinfo('履歴', '操作履歴はありません。')
            return
        lines = []
        for row in rows:
            try:
                owner = json.loads(row['owner'])
            except (TypeError, ValueError):
                owner = {}
            who = owner.get('tool') or owner.get('kind') or '不明'
            session = f" / {owner['session_id']}" if owner.get('session_id') else ''
            lines.append(f"{stamp(row['at'])}  {row['action']}  {row['device']}  {who}{session}")
        messagebox.showinfo('履歴', '\n'.join(lines[:100]))

    def autostart(self):
        from . import autostart
        try:
            if autostart.status():
                if messagebox.askyesno('自動起動', 'サインイン時の自動起動を解除しますか？'):
                    autostart.remove()
                    self.message.config(text='自動起動を解除しました。')
            elif messagebox.askyesno('自動起動', 'WindowsへサインインしたときにDevice Wardenを自動で起動しますか？'):
                autostart.install()
                self.message.config(text='自動起動を登録しました。次回のサインインから有効です。')
        except WardenError as exc:
            self.message.config(text=str(exc))

    def details(self):
        d = self.require_selection()
        if d:
            reasons = {'connection_lost': '接続切れまたは未承認', 'adb_unavailable': 'ADB異常',
                       'broker_restart': 'Broker再起動', 'heartbeat_timeout': 'heartbeat停止'}
            messagebox.showinfo('詳細', '\n'.join([f"端末ID: {d['id']}", f"所有者: {d['tool'] or d['kind'] or 'なし'}", f"セッションID: {d['session_id'] or '不明'}", f"タイトル: {d['title'] or '不明'}", f"プロジェクト: {d['project'] or '不明'}", f"作業ディレクトリ: {d['workdir'] or '不明'}", f"確保開始: {stamp(d['acquired_at'])}", f"最終heartbeat: {stamp(d['heartbeat_at'])}",
                f"要確認の理由: {reasons.get(d.get('recovery_reason'), '不明') if d['lease_state'] == 'recovery_required' else 'なし'}",
                f"検出時刻: {stamp(d.get('recovery_at'))}"]))

    def default_policy(self):
        answer = messagebox.askyesnocancel('新規端末のAI既定', '新規端末のAI利用を既定で許可しますか？\n「いいえ」で禁止します。')
        if answer is not None:
            self.run('default-policy', allowed=answer)


def main():
    import pystray
    from . import icon as warden_icon
    from .icon import icon_path
    root = tk.Tk()
    app = App(root)
    root.iconbitmap(str(icon_path()))
    root.iconbitmap(default=str(icon_path()))
    started = time.time()

    def bring_to_front():
        _force_foreground(root)

    def show(icon=None, item=None):
        root.after(0, bring_to_front)

    global _show_window
    _show_window = lambda: root.after(0, bring_to_front)

    def quit_app(icon, item):
        icon.stop()
        root.after(0, root.destroy)

    tray = pystray.Icon('Device Warden', warden_icon.image('starting'), 'Device Warden', menu=pystray.Menu(
        pystray.MenuItem('開く', show, default=True), pystray.MenuItem('終了', quit_app)))
    state = {'name': 'starting'}
    owners = {}

    def notify(title):
        # Notifications are best-effort; a tray backend without support must not
        # break the refresh loop.
        try:
            tray.notify(title, 'Device Warden')
        except (NotImplementedError, OSError):
            pass

    def notify_changes(rows):
        """Notify on lease changes only; never on every refresh."""
        current = {row['id']: (row['kind'], row['tool']) for row in rows}
        if not owners:
            owners.update(current)
            return
        for device_id, (kind, tool) in current.items():
            before = owners.get(device_id, (None, None))
            if before == (kind, tool):
                continue
            row = next(item for item in rows if item['id'] == device_id)
            if kind == 'ai':
                notify(f"{row['display_name']} を {tool or 'AI'} が確保しました")
            elif before[0] == 'ai' and kind is None:
                notify(f"{row['display_name']} が解放されました")
        owners.clear()
        owners.update(current)

    def sync_tray():
        """Reflect the most important device state in the tray icon."""
        try:
            rows = call('list', include_hidden=False)
            name = warden_icon.state_for(rows)
            notify_changes(rows)
        except WardenError:
            name = 'recovery' if time.time() - started > 20 else 'starting'
        if name != state['name']:
            state['name'] = name
            tray.icon = warden_icon.image(name)
            tray.title = f"Device Warden — {LABELS[name]}"
        root.after(10000, sync_tray)

    threading.Thread(target=tray.run, daemon=True).start()
    root.after(500, sync_tray)
    root.mainloop()


def _force_foreground(root):
    """Raise the window even when Windows says another process owns the foreground.

    A plain deiconify/lift is ignored when the request comes from a background
    process (the tray signal), so briefly attach to the foreground thread's input
    queue, which lets us call SetForegroundWindow legitimately.
    """
    root.deiconify()
    root.lift()
    root.update_idletasks()
    try:
        hwnd = int(root.wm_frame(), 16)
    except (tk.TclError, ValueError):
        return
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    foreground = user32.GetForegroundWindow()
    current = ctypes.WinDLL('kernel32').GetCurrentThreadId()
    target = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    attached = bool(foreground and target and target != current and user32.AttachThreadInput(target, current, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(target, current, False)
    root.focus_force()


LABELS = {
    'starting': '起動中',
    'free': '使用可能',
    'human': '自分が使用中',
    'ai': 'AI使用中',
    'recovery': '要確認',
    'idle': '端末なし',
}
_show_window = None


def show_window():
    """Bring an already running window to the front; used by a second launch."""
    if _show_window:
        _show_window()
        return True
    return False


if __name__ == '__main__':
    main()
