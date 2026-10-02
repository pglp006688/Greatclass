#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QFileDialog, QProgressBar,
    QFrame, QMessageBox, QScrollArea, QSizePolicy,
)


def find_ffmpeg():
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    candidates = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "bin" / exe)
        candidates.append(Path(meipass) / exe)

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates += [
            exe_dir / "bin" / exe,
            exe_dir / "_internal" / "bin" / exe,
            exe_dir / exe,
        ]

    here = Path(__file__).resolve().parent
    for p in (here, here.parent, here.parent.parent):
        candidates.append(p / "bin" / exe)
        candidates.append(p / exe)

    for c in candidates:
        if c.exists():
            return str(c)

    return shutil.which("ffmpeg")


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_duration(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class ProbeWorker(QThread):
    done = Signal(str, float)

    def __init__(self, video: str, ffmpeg: str, cover_out: Path):
        super().__init__()
        self.video = video
        self.ffmpeg = ffmpeg
        self.cover_out = cover_out

    def run(self):
        duration = 0.0
        cover = ""
        try:
            proc = subprocess.run(
                [self.ffmpeg, "-hide_banner", "-i", str(self.video)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="ignore", timeout=30,
            )
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr or "")
            if m:
                h, mi, s = m.groups()
                duration = int(h) * 3600 + int(mi) * 60 + float(s)
        except Exception:
            pass
        try:
            ts = "1" if duration > 2 else "0"
            subprocess.run(
                [self.ffmpeg, "-hide_banner", "-y", "-ss", ts,
                 "-i", str(self.video), "-frames:v", "1",
                 "-vf", "scale=-2:360", "-q:v", "4", str(self.cover_out)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=30, check=False,
            )
            if self.cover_out.exists() and self.cover_out.stat().st_size > 0:
                cover = str(self.cover_out)
        except Exception:
            pass
        self.done.emit(cover, duration)


class PackWorker(QThread):
    progress = Signal(int, str)
    success = Signal(str)
    failure = Signal(str)

    def __init__(self, payload: dict):
        super().__init__()
        self.p = payload
        self._cancel = False
        self._proc = None

    def cancel(self):
        self._cancel = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.kill()
            except Exception:
                pass

    def run(self):
        try:
            self._work()
        except Exception as e:
            if not self._cancel:
                import traceback
                traceback.print_exc()
                self.failure.emit(f"{type(e).__name__}: {e}")

    def _work(self):
        ffmpeg = self.p["ffmpeg"]
        lessons = self.p["lessons"]
        out_json = Path(self.p["out_json"])
        tmp = Path(tempfile.mkdtemp(prefix="gc_pack_"))
        n = len(lessons)

        try:
            processed = []
            for i, lesson in enumerate(lessons):
                if self._cancel:
                    return
                base = i / n * 100
                span = 100 / n
                src = Path(lesson["video"])

                self.progress.emit(int(base + span * 0.02),
                                   f"[{i+1}/{n}] 正在分析视频…")
                duration = self._probe(src, ffmpeg)
                if self._cancel:
                    return

                out_mp4 = tmp / f"lesson_{i}.mp4"
                self._compress(src, out_mp4, ffmpeg, duration, base, span, i, n)
                if self._cancel:
                    return
                if not out_mp4.exists() or out_mp4.stat().st_size == 0:
                    raise RuntimeError(f"第 {i+1} 集压缩失败")

                self.progress.emit(int(base + span * 0.88),
                                   f"[{i+1}/{n}] 正在生成封面…")
                cover_bytes, cover_mime = self._make_cover(src, tmp, ffmpeg, duration, i)

                self.progress.emit(int(base + span * 0.92),
                                   f"[{i+1}/{n}] 正在编码视频…")
                video_b64 = base64.b64encode(out_mp4.read_bytes()).decode("ascii")

                self.progress.emit(int(base + span * 0.97),
                                   f"[{i+1}/{n}] 正在编码封面…")
                cover_b64 = base64.b64encode(cover_bytes).decode("ascii")

                processed.append({
                    "id": str(uuid.uuid4()),
                    "title": lesson["title"],
                    "duration": round(duration, 2),
                    "video": {"mime": "video/mp4", "data": video_b64},
                    "cover": {"mime": cover_mime, "data": cover_b64},
                })

            course_cover = processed[0]["cover"] if processed else \
                {"mime": "image/png", "data": ""}

            self.progress.emit(98, "正在写入课程文件…")
            course = {
                "format": "greatclass.course",
                "version": 2,
                "id": str(uuid.uuid4()),
                "title": self.p["title"],
                "teacher": self.p["teacher"],
                "description": self.p["description"],
                "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                "cover": course_cover,
                "lessons": processed,
            }
            out_json.parent.mkdir(parents=True, exist_ok=True)
            out_json.write_text(
                json.dumps(course, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )

            self.progress.emit(100, "课程包生成完成")
            self.success.emit(str(out_json))

        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _probe(src: Path, ffmpeg: str) -> float:
        try:
            proc = subprocess.run(
                [ffmpeg, "-hide_banner", "-i", str(src)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="ignore", timeout=30,
            )
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr or "")
            if not m:
                return 0.0
            h, mi, s = m.groups()
            return int(h) * 3600 + int(mi) * 60 + float(s)
        except Exception:
            return 0.0

    def _compress(self, src, dst, ffmpeg, duration, base, span, idx, total):
        cmd = [
            ffmpeg, "-hide_banner", "-y",
            "-i", str(src),
            "-vf", "scale=-2:720",
            "-c:v", "libx264", "-preset", "medium", "-crf", "24",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-movflags", "+faststart",
            "-stats", "-stats_period", "0.5",
            str(dst),
        ]
        log_path = Path(self.p["out_json"]).with_suffix(f".lesson{idx+1}.log")
        log_file = open(log_path, "w", encoding="utf-8", errors="ignore")

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="ignore",
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        total_dur = duration if duration > 0 else 1.0
        time_re = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
        buf = ""
        while True:
            if self._cancel:
                self._proc.kill()
                self._proc.wait()
                log_file.close()
                self._proc = None
                return
            ch = self._proc.stdout.read(1)
            if not ch:
                break
            if ch == "\r":
                line, buf = buf.strip(), ""
                if line:
                    log_file.write(line + "\n")
                    log_file.flush()
                    m = time_re.search(line)
                    if m:
                        h, mi, s = m.groups()
                        sec = int(h) * 3600 + int(mi) * 60 + float(s)
                        pct_within = min(sec / total_dur, 1.0)
                        overall = base + span * (0.02 + pct_within * 0.85)
                        self.progress.emit(
                            int(overall),
                            f"[{idx+1}/{total}] 正在压缩… "
                            f"{fmt_duration(sec)} / {fmt_duration(total_dur)}"
                        )
            else:
                buf += ch

        self._proc.wait()
        code = self._proc.returncode
        log_file.close()
        self._proc = None

        if code != 0:
            try:
                log_text = log_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                log_text = ""
            raise RuntimeError(
                f"第 {idx+1} 集压缩失败（返回码 {code}）。\n\n"
                f"日志已保存到：{log_path}\n\n"
                f"———— 日志末尾 ————\n{log_text[-1500:]}"
            )

    def _make_cover(self, src, tmp, ffmpeg, duration, idx):
        dst = tmp / f"cover_{idx}.jpg"
        ts = "1" if duration > 2 else "0"
        subprocess.run(
            [ffmpeg, "-hide_banner", "-y", "-ss", ts, "-i", str(src),
             "-frames:v", "1", "-vf", "scale=-2:720", "-q:v", "3", str(dst)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if dst.exists() and dst.stat().st_size > 0:
            return dst.read_bytes(), "image/jpeg"
        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        ), "image/png"


class LessonCard(QFrame):
    remove_requested = Signal(object)

    def __init__(self, index: int, video_path: str):
        super().__init__()
        self.setObjectName("LessonCard")
        self.video_path = video_path
        self.duration = 0.0
        self._index = index

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)

        self.badge = QLabel(f"{index+1:02d}")
        self.badge.setObjectName("LessonBadge")
        self.badge.setFixedSize(40, 40)
        self.badge.setAlignment(Qt.AlignCenter)

        info = QVBoxLayout()
        info.setSpacing(3)

        self.ed_title = QLineEdit()
        self.ed_title.setObjectName("LessonTitle")
        self.ed_title.setPlaceholderText("输入本集标题")
        self.ed_title.setText(f"第 {index+1} 讲")

        self.lb_meta = QLabel(Path(video_path).name)
        self.lb_meta.setObjectName("LessonMeta")

        info.addWidget(self.ed_title)
        info.addWidget(self.lb_meta)

        self.btn_del = QPushButton("✕")
        self.btn_del.setObjectName("LessonDelete")
        self.btn_del.setFixedSize(30, 30)
        self.btn_del.clicked.connect(lambda: self.remove_requested.emit(self))

        lay.addWidget(self.badge)
        lay.addLayout(info, 1)
        lay.addWidget(self.btn_del)

    def set_index(self, i: int):
        self._index = i
        self.badge.setText(f"{i+1:02d}")
        if not self.ed_title.text().strip() or self.ed_title.text().startswith("第 "):
            self.ed_title.setText(f"第 {i+1} 讲")

    def set_duration(self, dur: float):
        self.duration = dur
        p = Path(self.video_path)
        text = f"{p.name}   ·   {human_size(p.stat().st_size)}"
        if dur > 0:
            text += f"   ·   {fmt_duration(dur)}"
        self.lb_meta.setText(text)

    def get_title(self) -> str:
        return self.ed_title.text().strip() or f"第 {self._index+1} 讲"


QSS = """
QMainWindow { background: #0d1117; }
QLabel { color: #c9d1d9; font-size: 13px; }
QLabel#Brand  { font-size: 30px; font-weight: 800; color: #ffffff; letter-spacing: 1px; }
QLabel#Sub    { color: #7d8590; font-size: 12px; }
QLabel#CardTitle { color: #ffffff; font-size: 14px; font-weight: 700; }
QLabel#Field  { color: #8b949e; font-size: 13px; }

QFrame#Card {
    background: #161b22; border: 1px solid #21262d; border-radius: 14px;
}

QFrame#LessonCard {
    background: #0d1117; border: 1px solid #21262d; border-radius: 10px;
}
QFrame#LessonCard:hover { border-color: #30363d; }

QLabel#LessonBadge {
    background: #1f6feb; color: #ffffff; border-radius: 8px;
    font-weight: 700; font-size: 14px;
}
QLabel#LessonMeta { color: #7d8590; font-size: 12px; }

QLineEdit, QTextEdit {
    background: #0d1117; border: 1px solid #30363d; border-radius: 9px;
    padding: 9px 12px; color: #e6edf3; selection-background-color: #1f6feb;
}
QLineEdit:focus, QTextEdit:focus { border: 1px solid #1f6feb; }
QLineEdit:disabled, QTextEdit:disabled { color: #484f58; background: #0b0f14; }

QLineEdit#LessonTitle {
    background: transparent; border: none;
    color: #e6edf3; font-size: 14px; font-weight: 600;
    padding: 2px 0;
}
QLineEdit#LessonTitle:focus {
    background: #0d1117; border: 1px solid #1f6feb;
    border-radius: 6px; padding: 2px 8px;
}

QPushButton {
    background: #21262d; border: 1px solid #30363d; border-radius: 9px;
    padding: 9px 18px; color: #e6edf3; font-weight: 500;
}
QPushButton:hover   { background: #30363d; border-color: #3d444d; }
QPushButton:pressed { background: #1f6feb; border-color: #1f6feb; }
QPushButton:disabled{ color: #484f58; background: #161b22; border-color: #21262d; }

QPushButton#Primary {
    background: #238636; border: 1px solid #2ea043;
    color: #ffffff; font-weight: 700; padding: 10px 26px;
}
QPushButton#Primary:hover   { background: #2ea043; }
QPushButton#Primary:pressed { background: #1a7f37; }

QPushButton#Danger {
    background: #b62324; border: 1px solid #d1242f;
    color: #ffffff; font-weight: 700; padding: 10px 26px;
}
QPushButton#Danger:hover { background: #d1242f; }

QPushButton#AddBtn {
    background: #161b22; border: 1px dashed #30363d;
    color: #58a6ff; font-weight: 600; padding: 12px;
}
QPushButton#AddBtn:hover {
    background: #0d1117; border-color: #1f6feb; color: #79c0ff;
}

QPushButton#LessonDelete {
    background: transparent; border: none; color: #7d8590;
    font-size: 14px; padding: 0;
}
QPushButton#LessonDelete:hover {
    background: #b62324; color: #ffffff; border-radius: 15px;
}

QProgressBar { background: #161b22; border: none; border-radius: 5px; }
QProgressBar::chunk {
    border-radius: 5px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #1f6feb, stop:1 #2ea043);
}

QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #30363d; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("GreatClass · 教师端")
        self.resize(1020, 820)
        self.setMinimumSize(900, 720)

        self.lessons = []
        self.busy = False
        self.worker = None
        self._probe_threads = []

        self._build_ui()
        self._check_ffmpeg()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(16)

        brand = QLabel("GreatClass")
        brand.setObjectName("Brand")
        tagline = QLabel("教师端 · 多集上传 → 自动 720p 压缩 → 生成课程包")
        tagline.setObjectName("Sub")
        outer.addWidget(brand)
        outer.addWidget(tagline)
        outer.addSpacing(4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(0, 0, 0, 0)
        inner_lay.setSpacing(16)

        inner_lay.addWidget(self._build_info_card())
        inner_lay.addWidget(self._build_lessons_card(), 1)

        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)

        self.status = QLabel("就绪")
        self.status.setObjectName("Sub")

        self.btn_pack = QPushButton("生成课程包")
        self.btn_pack.setObjectName("Primary")
        self.btn_pack.setMinimumHeight(42)
        self.btn_pack.clicked.connect(self.start_pack)

        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.btn_pack)

        outer.addWidget(self.bar)
        outer.addLayout(bottom)

    def _build_info_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 16, 18, 18)
        v.setSpacing(13)

        title = QLabel("课程信息")
        title.setObjectName("CardTitle")
        v.addWidget(title)

        form = QGridLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(11)

        self.ed_title = QLineEdit()
        self.ed_title.setPlaceholderText("例如：Python 从入门到实战")
        self.ed_teacher = QLineEdit()
        self.ed_teacher.setPlaceholderText("例如：张老师")
        self.ed_desc = QTextEdit()
        self.ed_desc.setPlaceholderText("简要介绍本课程（可选）")
        self.ed_desc.setFixedHeight(72)

        form.addWidget(self._field("课程名称 *"), 0, 0)
        form.addWidget(self.ed_title, 0, 1)
        form.addWidget(self._field("讲师 *"), 1, 0)
        form.addWidget(self.ed_teacher, 1, 1)
        form.addWidget(self._field("课程简介"), 2, 0, Qt.AlignTop)
        form.addWidget(self.ed_desc, 2, 1)
        form.setColumnStretch(1, 1)
        v.addLayout(form)

        return card

    def _build_lessons_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 16, 18, 18)
        v.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("课程集数")
        title.setObjectName("CardTitle")
        self.lb_count = QLabel("共 0 集")
        self.lb_count.setObjectName("Sub")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.lb_count)
        v.addLayout(header)

        self.lesson_holder = QWidget()
        self.lesson_layout = QVBoxLayout(self.lesson_holder)
        self.lesson_layout.setContentsMargins(0, 0, 0, 0)
        self.lesson_layout.setSpacing(8)
        self.lesson_layout.addStretch()

        self.lesson_scroll = QScrollArea()
        self.lesson_scroll.setWidgetResizable(True)
        self.lesson_scroll.setFrameShape(QFrame.NoFrame)
        self.lesson_scroll.setWidget(self.lesson_holder)
        self.lesson_scroll.setMinimumHeight(180)
        v.addWidget(self.lesson_scroll, 1)

        self.btn_add = QPushButton("＋  添加视频")
        self.btn_add.setObjectName("AddBtn")
        self.btn_add.setMinimumHeight(46)
        self.btn_add.clicked.connect(self.add_lessons)
        v.addWidget(self.btn_add)

        return card

    @staticmethod
    def _field(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("Field")
        return lbl

    def _check_ffmpeg(self):
        path = find_ffmpeg()
        if path:
            self.status.setText(f"ffmpeg 已就绪：{path}")
        else:
            self.status.setText("⚠ 未找到 ffmpeg —— 请放入项目 bin/ 目录或加入系统 PATH")

    def add_lessons(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择视频（可多选）", "",
            "视频文件 (*.mp4 *.mov *.mkv *.avi *.webm *.flv *.m4v *.ts *.wmv);;所有文件 (*.*)",
        )
        if not paths:
            return
        for p in paths:
            self._append_lesson(p)

    def _append_lesson(self, path: str):
        card = LessonCard(len(self.lessons), path)
        card.remove_requested.connect(self._remove_lesson)
        self.lessons.append(card)
        self.lesson_layout.insertWidget(self.lesson_layout.count() - 1, card)
        self._refresh_indexes()

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            return
        out = Path(tempfile.gettempdir()) / f"gc_probe_{uuid.uuid4().hex[:8]}.jpg"
        th = ProbeWorker(path, ffmpeg, out)

        def on_done(cover, dur, c=card):
            c.set_duration(dur)

        th.done.connect(on_done)
        th.finished.connect(lambda t=th: self._probe_threads.remove(t) if t in self._probe_threads else None)
        self._probe_threads.append(th)
        th.start()

    def _remove_lesson(self, card: LessonCard):
        if self.busy:
            return
        if card in self.lessons:
            self.lessons.remove(card)
        card.setParent(None)
        card.deleteLater()
        self._refresh_indexes()

    def _refresh_indexes(self):
        for i, card in enumerate(self.lessons):
            card.set_index(i)
        self.lb_count.setText(f"共 {len(self.lessons)} 集")

    def start_pack(self):
        if self.busy:
            if self.worker:
                self.worker.cancel()
            self.status.setText("正在取消…")
            return

        title = self.ed_title.text().strip()
        teacher = self.ed_teacher.text().strip()

        if not title:
            return self._warn("请填写课程名称")
        if not teacher:
            return self._warn("请填写讲师姓名")
        if not self.lessons:
            return self._warn("请至少添加一集视频")

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            return self._warn(
                "未找到 ffmpeg。\n\n"
                "请把 ffmpeg 可执行文件放到项目根目录的 bin/ 文件夹中，\n"
                "或者安装 ffmpeg 并加入系统 PATH。"
            )

        safe = re.sub(r'[\\/:*?"<>|]', "_", f"{title}-{teacher}")
        out_path, _ = QFileDialog.getSaveFileName(
            self, "保存课程包", f"{safe}.gclass.json", "GreatClass 课程包 (*.json)"
        )
        if not out_path:
            return
        if not out_path.lower().endswith(".json"):
            out_path += ".json"

        lessons_data = []
        for card in self.lessons:
            lessons_data.append({
                "video": card.video_path,
                "title": card.get_title(),
            })

        payload = {
            "lessons": lessons_data,
            "title": title,
            "teacher": teacher,
            "description": self.ed_desc.toPlainText().strip(),
            "out_json": out_path,
            "ffmpeg": ffmpeg,
        }

        self._set_busy(True)
        self.bar.setValue(0)

        self.worker = PackWorker(payload)
        self.worker.progress.connect(self._on_progress)
        self.worker.success.connect(self._on_success)
        self.worker.failure.connect(self._on_failure)
        self.worker.start()

    def _set_busy(self, busy: bool):
        self.busy = busy
        self.ed_title.setEnabled(not busy)
        self.ed_teacher.setEnabled(not busy)
        self.ed_desc.setEnabled(not busy)
        self.btn_add.setEnabled(not busy)
        for card in self.lessons:
            card.ed_title.setEnabled(not busy)
            card.btn_del.setEnabled(not busy)

        if busy:
            self.btn_pack.setText("取消打包")
            self.btn_pack.setObjectName("Danger")
        else:
            self.btn_pack.setText("生成课程包")
            self.btn_pack.setObjectName("Primary")

        self.btn_pack.style().unpolish(self.btn_pack)
        self.btn_pack.style().polish(self.btn_pack)

    def _on_progress(self, pct: int, text: str):
        self.bar.setValue(pct)
        self.status.setText(text)

    def _on_success(self, path: str):
        self._set_busy(False)
        self.bar.setValue(100)
        size = human_size(Path(path).stat().st_size)
        self.status.setText(f"课程包已生成 · {size}")
        QMessageBox.information(
            self, "打包完成",
            f"课程包已生成：\n{path}\n\n"
            f"文件大小：{size}\n"
            f"共 {len(self.lessons)} 集\n\n"
            "把这个 JSON 文件发给学生，导入即可观看。"
        )

    def _on_failure(self, msg: str):
        self._set_busy(False)
        self.bar.setValue(0)
        self.status.setText("打包失败")
        if "取消" not in msg:
            QMessageBox.critical(self, "打包失败", msg)

    @staticmethod
    def _warn(msg: str):
        QMessageBox.warning(None, "提示", msg)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("GreatClass Teacher")
    app.setStyleSheet(QSS)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
