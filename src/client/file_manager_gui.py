import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import sys
import os
import threading
import io

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../common")))

try:
    from PIL import Image, ImageTk, ImageDraw
except ImportError as e:
    messagebox.showerror(
        "Dependency Error",
        f"Failed to import required modules: {e}\nPlease ensure 'Pillow' is installed.",
    )
    sys.exit(1)

try:
    from network_manager import NetworkManager
except ImportError:
    try:
        from src.client.network_manager import NetworkManager
    except ImportError:
        from .network_manager import NetworkManager


class StorageGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Object Storage Manager")
        self.root.geometry("850x600")

        self.client = NetworkManager(self.log_message)

        style = ttk.Style()
        style.theme_use("clam")

        bg_color = "#f5f5f5"
        fg_color = "#333333"
        panel_bg = "#ffffff"
        accent_color = "#0078d7"

        self.root.configure(bg=bg_color)

        style.configure("TFrame", background=bg_color)
        style.configure(
            "TLabelframe",
            background=bg_color,
            foreground=fg_color,
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "TLabelframe.Label",
            background=bg_color,
            foreground="#555555",
            font=("Segoe UI", 10, "bold"),
        )

        style.configure(
            "TButton",
            font=("Segoe UI", 10),
            borderwidth=0,
            background=panel_bg,
            foreground=fg_color,
        )
        style.map(
            "TButton",
            background=[("active", "#e1e1e1")],
            foreground=[("active", "#000000")],
        )

        style.configure(
            "TLabel", background=bg_color, foreground=fg_color, font=("Segoe UI", 10)
        )

        style.configure(
            "Treeview",
            background=panel_bg,
            fieldbackground=panel_bg,
            foreground=fg_color,
            font=("Segoe UI", 10),
            rowheight=25,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#e1e1e1",
            foreground="#333333",
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Treeview",
            background=[("selected", accent_color)],
            foreground=[("selected", "white")],
        )

        top_frame = ttk.Frame(root, padding=(20, 20, 20, 10))
        top_frame.pack(fill=tk.X)

        title_lbl = ttk.Label(
            top_frame,
            text="Object Storage Client",
            font=("Segoe UI", 18, "bold"),
            foreground="#333333",
        )
        title_lbl.pack(side=tk.TOP, anchor="w", pady=(0, 5))

        status_frame = ttk.Frame(top_frame)
        status_frame.pack(fill=tk.X, pady=5)

        self.lbl_status = ttk.Label(
            status_frame,
            text="● Disconnected",
            foreground="#dc3545",
            font=("Segoe UI", 10),
        )
        self.lbl_status.pack(side=tk.LEFT, padx=(0, 15))

        self.icons = {}

        def simple_icon(color):
            img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.polygon(
                [(3, 1), (9, 1), (13, 5), (13, 14), (3, 14)],
                fill=color,
                outline="#666666",
            )
            draw.polygon([(9, 1), (9, 5), (13, 5)], fill="#FFFFFF80")
            return ImageTk.PhotoImage(img)

        self.icons["image"] = simple_icon("#9C27B0")
        self.icons["text"] = simple_icon("#2196F3")
        self.icons["code"] = simple_icon("#FF9800")
        self.icons["archive"] = simple_icon("#F44336")
        self.icons["default"] = simple_icon("#9E9E9E")

        self.ext_map = {
            "png": "image",
            "jpg": "image",
            "jpeg": "image",
            "gif": "image",
            "bmp": "image",
            "webp": "image",
            "txt": "text",
            "md": "text",
            "log": "text",
            "csv": "text",
            "py": "code",
            "js": "code",
            "html": "code",
            "css": "code",
            "json": "code",
            "xml": "code",
            "java": "code",
            "c": "code",
            "cpp": "code",
            "zip": "archive",
            "tar": "archive",
            "gz": "archive",
            "rar": "archive",
            "7z": "archive",
        }

        content_frame = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))

        left_pane = ttk.LabelFrame(content_frame, text="Files", padding=10)
        content_frame.add(left_pane, weight=2)

        columns = ("Size",)
        self.tree_files = ttk.Treeview(
            left_pane, columns=columns, show="tree headings", style="Treeview"
        )
        self.tree_files.heading("#0", text="Name", anchor="w")
        self.tree_files.heading("Size", text="Size", anchor="e")
        self.tree_files.column("#0", width=300)
        self.tree_files.column("Size", width=80, anchor="e")

        self.tree_files.bind("<<TreeviewSelect>>", self.on_file_select)

        scrollbar = ttk.Scrollbar(
            left_pane, orient=tk.VERTICAL, command=self.tree_files.yview
        )
        self.tree_files.configure(yscroll=scrollbar.set)

        self.tree_files.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        btn_refresh = ttk.Button(
            left_pane, text="↻ Refresh List", command=self.refresh_lists
        )
        btn_refresh.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))

        right_pane = ttk.LabelFrame(content_frame, text="Preview", padding=10)
        content_frame.add(right_pane, weight=1)

        self.lbl_preview_info = ttk.Label(
            right_pane, text="Select a file to preview", anchor="center"
        )
        self.lbl_preview_info.pack(side=tk.TOP, fill=tk.X, pady=(0, 5))

        self.preview_container = ttk.Frame(right_pane)
        self.preview_container.pack(fill=tk.BOTH, expand=True)

        self.lbl_image_preview = tk.Label(self.preview_container, bg="white")
        self.txt_text_preview = tk.Text(
            self.preview_container, wrap="none", state="disabled", font=("Consolas", 8)
        )

        action_frame = ttk.Frame(root, padding=(20, 10))
        action_frame.pack(fill=tk.X)

        ttk.Button(action_frame, text="⬆ Upload", command=self.on_upload).pack(
            side=tk.LEFT, padx=(0, 10)
        )
        ttk.Button(
            action_frame,
            text="⬇ Download (TCP)",
            command=lambda: self.on_download("TCP"),
        ).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(
            action_frame,
            text="⬇ Download (RUDP)",
            command=lambda: self.on_download("RUDP"),
        ).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(action_frame, text="🗑 Delete", command=self.on_delete).pack(
            side=tk.RIGHT
        )

        log_frame = ttk.Frame(root, padding=(20, 0, 20, 20))
        log_frame.pack(fill=tk.X)

        lbl_log = ttk.Label(
            log_frame,
            text="Activity Log",
            font=("Segoe UI", 9, "bold"),
            foreground="#666666",
        )
        lbl_log.pack(anchor="w", pady=(5, 2))

        self.txt_log = scrolledtext.ScrolledText(
            log_frame,
            height=6,
            state="disabled",
            font=("Consolas", 9),
            bg="#ffffff",
            fg="#333333",
            relief="flat",
            borderwidth=1,
        )
        self.txt_log.pack(fill=tk.X)

        log_border = tk.Frame(log_frame, background="#cccccc")
        self.txt_log.pack(in_=log_border, padx=1, pady=1, fill=tk.X)
        log_border.pack(fill=tk.X)

        self.root.after(100, self.start_connection)

    def log_message(self, msg):
        self.txt_log.config(state="normal")
        self.txt_log.insert(tk.END, msg + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.config(state="disabled")
        print(msg)

    def start_connection(self):
        self.lbl_status.config(text="Status: Connecting...", foreground="orange")
        threading.Thread(target=self._connect_thread, daemon=True).start()

    def _connect_thread(self):
        success = self.client.connect_sequence()
        if success:
            self.root.after(0, lambda: self._on_connect_success())
        else:
            self.root.after(0, lambda: self._on_connect_fail())

    def _on_connect_success(self):
        self.lbl_status.config(
            text=f"● Connected ({self.client.server_ip})", foreground="#28a745"
        )
        self.refresh_lists()

    def _on_connect_fail(self):
        self.lbl_status.config(text="● Connection Failed", foreground="#dc3545")
        messagebox.showerror(
            "Connection Error",
            "Could not connect to DHCP or DNS server.\nEnsure servers are running.",
        )

    def refresh_lists(self):
        threading.Thread(target=self._refresh_thread, daemon=True).start()

    def on_file_select(self, event):
        selected = self.tree_files.selection()
        if not selected:
            return

        filename = self.tree_files.item(selected[0])["text"]
        self.lbl_image_preview.pack_forget()
        self.txt_text_preview.pack_forget()
        self.lbl_preview_info.config(text=f"Loading preview for {filename}...")

        threading.Thread(
            target=lambda: self._load_preview(filename), daemon=True
        ).start()

    def _load_preview(self, filename):
        data = self.client.fetch_file_bytes(filename)
        self.root.after(0, lambda: self._show_preview_data(filename, data))

    def _show_preview_data(self, filename, data):
        if data is None:
            self.lbl_preview_info.config(
                text="Preview not available (Too large or Error)"
            )
            return

        self.lbl_preview_info.config(text=f"Preview: {filename}")

        try:
            image_stream = io.BytesIO(data)
            pil_image = Image.open(image_stream)

            pil_image.thumbnail((300, 300))

            img = ImageTk.PhotoImage(pil_image)
            self.lbl_image_preview.config(image=img)
            self.lbl_image_preview.image = img
            self.lbl_image_preview.pack(fill=tk.BOTH, expand=True)
            return
        except Exception:
            pass

        try:
            text_content = data.decode("utf-8")
            if len(text_content) > 2000:
                text_content = text_content[:2000] + "\n...[Truncated]"

            self.txt_text_preview.config(state="normal")
            self.txt_text_preview.delete("1.0", tk.END)
            self.txt_text_preview.insert("1.0", text_content)
            self.txt_text_preview.config(state="disabled")
            self.txt_text_preview.pack(fill=tk.BOTH, expand=True)
            return
        except UnicodeDecodeError:
            self.lbl_preview_info.config(text="Binary file (No preview)")

    def _refresh_thread(self):
        if not self.client.connected:
            return

        files = self.client.list_files()

        self.root.after(0, lambda: self._update_lists(files))

    def _update_lists(self, files):
        for item in self.tree_files.get_children():
            self.tree_files.delete(item)

        for name, size in files:
            ext = name.split(".")[-1].lower() if "." in name else ""
            icon_type = self.ext_map.get(ext, "default")
            icon = self.icons.get(icon_type, self.icons["default"])

            self.tree_files.insert("", tk.END, text=name, image=icon, values=(size,))

    def on_upload(self):
        path = filedialog.askopenfilename()
        if path:
            threading.Thread(
                target=lambda: self._upload_thread(path), daemon=True
            ).start()

    def _upload_thread(self, path):
        if self.client.upload_file(path):
            self.refresh_lists()

    def on_delete(self):
        selected = self.tree_files.selection()
        if not selected:
            messagebox.showwarning("Select File", "Please select a file to delete.")
            return

        filename = self.tree_files.item(selected[0])["text"]
        if messagebox.askyesno("Confirm Delete", f"Delete {filename}?"):
            threading.Thread(
                target=lambda: self.client.delete_file(filename)
                and self.refresh_lists(),
                daemon=True,
            ).start()

    def on_download(self, protocol):
        selected = self.tree_files.selection()
        if not selected:
            messagebox.showwarning("Select File", "Please select a file to download.")
            return

        filename = self.tree_files.item(selected[0])["text"]
        save_path = filedialog.asksaveasfilename(initialfile=filename)

        if save_path:
            if protocol == "TCP":
                threading.Thread(
                    target=lambda: self.client.download_file_tcp(filename, save_path),
                    daemon=True,
                ).start()
            else:
                threading.Thread(
                    target=lambda: self.client.download_file_rudp(filename, save_path),
                    daemon=True,
                ).start()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--no-gui":
        print("This script is designed to run with a GUI.")
        sys.exit(0)

    root = tk.Tk()
    app = StorageGUI(root)
    root.mainloop()
