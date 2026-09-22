from __future__ import annotations

import os
import queue
import subprocess
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .excel_writer import write_excel
from .extractor import convert_folder, find_invoice_files


class InvoiceConverterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("发票批量转 Excel")
        self.geometry("820x600")
        self.minsize(720, 520)
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        self.folder_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="请选择发票目录")
        self.progress_var = tk.DoubleVar(value=0)
        self.cancel_event = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.last_output: Path | None = None
        self._build_ui()
        self.after(100, self._poll_events)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"))

        root = ttk.Frame(self, padding=22)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="发票批量转 Excel", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        ttk.Label(root, text="支持 PDF、JPG/JPEG；电子 PDF 优先读取文字，扫描件自动 OCR。", foreground="#555555").pack(anchor="w", pady=(4, 20))

        form = ttk.Frame(root)
        form.pack(fill="x")
        ttk.Label(form, text="发票目录", width=10).grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(form, textvariable=self.folder_var).grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=6)
        ttk.Button(form, text="选择目录", command=self._choose_folder).grid(row=0, column=2, pady=6)
        ttk.Label(form, text="输出文件", width=10).grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(form, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=6)
        ttk.Button(form, text="选择文件", command=self._choose_output).grid(row=1, column=2, pady=6)
        form.columnconfigure(1, weight=1)

        options = ttk.Frame(root)
        options.pack(fill="x", pady=(8, 14))
        ttk.Checkbutton(options, text="包含子目录", variable=self.recursive_var).pack(side="left")
        ttk.Label(options, text="重复发票号会保留并标记为需复核", foreground="#666666").pack(side="left", padx=18)

        actions = ttk.Frame(root)
        actions.pack(fill="x")
        self.start_button = ttk.Button(actions, text="开始转换", style="Accent.TButton", command=self._start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(actions, text="取消", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        self.open_button = ttk.Button(actions, text="打开输出目录", command=self._open_output, state="disabled")
        self.open_button.pack(side="left")

        self.progress = ttk.Progressbar(root, variable=self.progress_var, maximum=100)
        self.progress.pack(fill="x", pady=(18, 6))
        ttk.Label(root, textvariable=self.status_var).pack(anchor="w")

        log_frame = ttk.LabelFrame(root, text="处理日志", padding=8)
        log_frame.pack(fill="both", expand=True, pady=(14, 0))
        self.log = tk.Text(log_frame, height=14, wrap="word", state="disabled", bg="#FAFAFA", relief="flat")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _choose_folder(self) -> None:
        folder = filedialog.askdirectory(title="选择发票目录")
        if not folder:
            return
        self.folder_var.set(folder)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_var.set(str(Path(folder) / f"发票汇总_{timestamp}.xlsx"))
        count = len(find_invoice_files(folder, self.recursive_var.get()))
        self.status_var.set(f"找到 {count} 个 PDF/JPG 文件")

    def _choose_output(self) -> None:
        initial = self.output_var.get() or "发票汇总.xlsx"
        output = filedialog.asksaveasfilename(
            title="保存 Excel",
            initialfile=Path(initial).name,
            defaultextension=".xlsx",
            filetypes=[("Excel 工作簿", "*.xlsx")],
        )
        if output:
            self.output_var.set(output)

    def _start(self) -> None:
        folder = Path(self.folder_var.get().strip())
        output = Path(self.output_var.get().strip())
        if not folder.is_dir():
            messagebox.showwarning("请选择目录", "请选择有效的发票目录。")
            return
        if not output.name:
            messagebox.showwarning("请选择输出文件", "请指定 Excel 输出文件。")
            return
        files = find_invoice_files(folder, self.recursive_var.get())
        if not files:
            messagebox.showinfo("没有文件", "目录中没有找到 PDF、JPG 或 JPEG 文件。")
            return
        self.cancel_event.clear()
        self.progress_var.set(0)
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.open_button.configure(state="disabled")
        self._append_log(f"开始处理，共 {len(files)} 个文件。")
        recursive = self.recursive_var.get()
        thread = threading.Thread(target=self._worker, args=(folder, output, recursive), daemon=True)
        thread.start()

    def _worker(self, folder: Path, output: Path, recursive: bool) -> None:
        def progress(index: int, total: int, path: Path) -> None:
            self.events.put(("progress", index, total, path.name))

        try:
            records, errors = convert_folder(
                folder,
                recursive=recursive,
                progress=progress,
                should_cancel=self.cancel_event.is_set,
            )
            if not records and self.cancel_event.is_set():
                self.events.put(("cancelled",))
                return
            saved = write_excel(records, output)
            self.events.put(("done", saved, records, errors, self.cancel_event.is_set()))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.status_var.set("正在停止；当前文件完成后结束……")
        self.cancel_button.configure(state="disabled")

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "progress":
                    _, index, total, name = event
                    self.progress_var.set((index - 1) / max(total, 1) * 100)
                    self.status_var.set(f"正在处理 {index}/{total}：{name}")
                    self._append_log(f"[{index}/{total}] {name}")
                elif kind == "done":
                    _, saved, records, errors, cancelled = event
                    self.last_output = saved
                    self.progress_var.set(100 if not cancelled else self.progress_var.get())
                    ok = sum(record.status == "成功" for record in records)
                    review = sum(record.status == "需复核" for record in records)
                    failed = sum(record.status == "失败" for record in records)
                    prefix = "已取消，部分结果已保存" if cancelled else "转换完成"
                    summary = f"{prefix}：共 {len(records)} 行，成功 {ok}，需复核 {review}，失败 {failed}。"
                    self.status_var.set(summary)
                    self._append_log(summary)
                    for error in errors:
                        self._append_log("错误：" + error)
                    self._set_idle()
                    self.open_button.configure(state="normal")
                    messagebox.showinfo("完成", f"{summary}\n\n输出：{saved}")
                elif kind == "cancelled":
                    self.status_var.set("已取消，未生成结果。")
                    self._set_idle()
                elif kind == "error":
                    self._set_idle()
                    self.status_var.set("转换失败")
                    self._append_log("失败：" + event[1])
                    messagebox.showerror("转换失败", event[1])
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _set_idle(self) -> None:
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _open_output(self) -> None:
        if not self.last_output:
            return
        folder = str(self.last_output.parent)
        if os.name == "nt":
            os.startfile(folder)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", folder])


def run() -> None:
    app = InvoiceConverterApp()
    app.mainloop()
