#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import json
import shutil
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QSize, QEvent
from PySide6.QtGui import QPixmap, QShortcut, QKeySequence, QFont, QCursor
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QFrame, QMessageBox,
    QSlider, QStackedWidget, QSizePolicy, QScrollArea, QListWidget,
    QListWidgetItem,
)


def fmt_time(ms: int) -> str:
    if ms < 0:
        ms = 0
    sec = ms // 1000
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class CourseLoader(QThread):
    progress = Signal(int, str)
    loaded = Signal(dict, list)
    failed = Signal(str)

    def __init__(self, json_path: str, work_dir: Path):
        super().__init__()
        self.json_path = json_path
        self.work_dir = work_dir

    def run(self):
        try:
            self.progress.emit(3, "正在读取课程包…")
            raw = Path(self.json_path).read_text(encoding="utf-8")

            self.progress.emit(15, "正在解析课程数据…")
            data = json.loads(raw)

            if data.get("format") != "greatclass.course":
                raise ValueError("这不是有效的 GreatClass 课程包")

            meta = {
                "title": data.get("title", "未命名课程"),
                "teacher": data.get("teacher", "未知讲师"),
                "description": data.get("description", ""),
                "created_at": data.get("created_at", ""),
            }

            cover_b64 = (data.get("cover") or {}).get("data", "")
            if cover_b64:
                cover_bytes = base64.b64decode(cover_b64)
                ext = ".png" if data["cover"].get("mime") == "image/png" else ".jpg"
                cp = self.work_dir / f"course_cover{ext}"
                cp.write_bytes(cover_bytes)
                meta["cover_path"] = str(cp)
            else:
                meta["cover_path"] = ""

            lessons_raw = data.get("lessons")
            if not lessons_raw:
                video_b64 = (data.get("video") or {}).get("data", "")
                if not video_b64:
                    raise ValueError("课程包中缺少视频数据")
                lessons_raw = [{
                    "title": meta["title"],
                    "duration": data.get("duration", 0),
                    "video": data.get("video"),
                    "cover": data.get("cover"),
                }]

            n = len(lessons_raw)
            lessons = []
            for i, l in enumerate(lessons_raw):
                self.progress.emit(
                    20 + int((i / n) * 75),
                    f"正在还原第 {i+1}/{n} 集…"
                )

                vb64 = (l.get("video") or {}).get("data", "")
                if not vb64:
                    continue
                vbytes = base64.b64decode(vb64)
                vp = self.work_dir / f"lesson_{i}.mp4"
                vp.write_bytes(vbytes)

                cover_path = ""
                cb64 = (l.get("cover") or {}).get("data", "")
                if cb64:
                    cbytes = base64.b64decode(cb64)
                    cext = ".png" if l["cover"].get("mime") == "image/png" else ".jpg"
                    cp = self.work_dir / f"lesson_{i}_cover{cext}"
                    cp.write_bytes(cbytes)
                    cover_path = str(cp)

                lessons.append({
                    "title": l.get("title", f"第 {i+1} 集"),
                    "duration": l.get("duration", 0),
                    "video_path": str(vp),
                    "cover_path": cover_path,
                })

            self.progress.emit(100, "加载完成")
            self.loaded.emit(meta, lessons)

        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")


QSS = """
QMainWindow { background: #f5f7fa; }
QWidget#Root { background: #f5f7fa; }

QWidget#TopBar { background: #ffffff; border-bottom: 1px solid #ebedf0; }
QLabel#Logo { color: #00b578; font-size: 20px; font-weight: 800; letter-spacing: 1px; }
QLabel#TopSlogan { color: #969ba5; font-size: 12px; }

QLabel { color: #1f2329; font-size: 13px; }
QLabel#SubTitle  { color: #969ba5; font-size: 12px; }
QLabel#CardTitle { color: #1f2329; font-size: 15px; font-weight: 700; }

QLabel#CourseTitle {
    color: #1f2329; font-size: 17px; font-weight: 700;
}
QLabel#TeacherTag {
    color: #00b578; background: #e8f7f0; font-size: 12px;
    padding: 4px 10px; border-radius: 10px; font-weight: 600;
}
QLabel#MetaText { color: #969ba5; font-size: 12px; }
QLabel#DescText { color: #4e5969; font-size: 12px; }

QLabel#EmptyIcon { font-size: 56px; }
QLabel#EmptyTitle { color: #1f2329; font-size: 18px; font-weight: 700; }
QLabel#EmptyDesc { color: #969ba5; font-size: 13px; }

QFrame#Card {
    background: #ffffff; border: 1px solid #ebedf0; border-radius: 10px;
}
QFrame#VideoFrame {
    background: #000000; border-radius: 8px;
}

QPushButton {
    background: #f2f3f5; border: 1px solid #ebedf0; border-radius: 6px;
    padding: 8px 18px; color: #1f2329; font-weight: 500;
}
QPushButton:hover   { background: #e8eaed; }
QPushButton:pressed { background: #dee0e3; }

QPushButton#Primary {
    background: #00b578; border: 1px solid #00b578;
    color: #ffffff; font-weight: 600; padding: 9px 22px;
}
QPushButton#Primary:hover   { background: #00a06b; border-color: #00a06b; }
QPushButton#Primary:pressed { background: #008a5c; }
QPushButton#Primary:disabled { background: #b8e5d2; border-color: #b8e5d2; color: #ffffff; }

QPushButton#Ghost {
    background: #ffffff; border: 1px solid #ebedf0; color: #4e5969;
}
QPushButton#Ghost:hover { border-color: #00b578; color: #00b578; }
QPushButton#Ghost:disabled { color: #c9cdd4; border-color: #f2f3f5; }

QPushButton#PlayBtn {
    background: #00b578; border: none;
    color: #ffffff; font-weight: 700;
    min-width: 44px; min-height: 44px; max-width: 44px; max-height: 44px;
    border-radius: 22px; font-size: 15px;
}
QPushButton#PlayBtn:hover   { background: #00a06b; }
QPushButton#PlayBtn:pressed { background: #008a5c; }

QPushButton#SmallRound {
    background: #ffffff; border: 1px solid #ebedf0;
    color: #4e5969; font-weight: 700;
    min-width: 34px; min-height: 34px; max-width: 34px; max-height: 34px;
    border-radius: 17px;
}
QPushButton#SmallRound:hover { border-color: #00b578; color: #00b578; }

QSlider::groove:horizontal {
    background: #e5e6eb; height: 4px; border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #00b578; border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #ffffff; width: 14px; margin: -6px 0;
    border-radius: 7px; border: 2px solid #00b578;
}
QSlider::handle:horizontal:hover { background: #e8f7f0; }

QSlider#Volume::groove:horizontal { height: 4px; }
QSlider#Volume::sub-page:horizontal { background: #c9cdd4; }
QSlider#Volume::handle:horizontal {
    width: 12px; margin: -5px 0; border-radius: 6px;
    border: 2px solid #86909c; background: #ffffff;
}
QSlider#Volume::handle:horizontal:hover { border-color: #00b578; }

QListWidget {
    background: transparent; border: none;
    outline: 0;
}
QListWidget::item {
    padding: 11px 12px;
    border-radius: 6px;
    color: #4e5969;
    margin-bottom: 3px;
}
QListWidget::item:hover {
    background: #f2f3f5;
}
QListWidget::item:selected {
    background: #e8f7f0;
    color: #00b578;
    font-weight: 600;
}

QScrollArea { background: transparent; border: none; }
QScrollBar:vertical {
    background: transparent; width: 8px; margin: 0;
}
QScrollBar::handle:vertical {
    background: #d5d8dd; border-radius: 4px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #b8bcc4; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


FS_QSS = """
QWidget#FSRoot { background: #000000; }
QWidget#FSBar  { background: rgba(0, 0, 0, 190); }

QPushButton#FSBtn {
    background: transparent; border: none;
    color: #ffffff; font-size: 16px;
    min-width: 40px; min-height: 40px;
    padding: 0 10px;
}
QPushButton#FSBtn:hover { background: rgba(255,255,255,40); border-radius: 6px; }
QPushButton#FSBtn:pressed { background: rgba(255,255,255,80); }

QPushButton#FSPlayBtn {
    background: rgba(255,255,255,30); border: none;
    color: #ffffff; font-weight: 700;
    min-width: 40px; min-height: 40px;
    max-width: 40px; max-height: 40px;
    border-radius: 20px; font-size: 15px;
}
QPushButton#FSPlayBtn:hover { background: rgba(255,255,255,60); }

QLabel#FSTime { color: #ffffff; font-size: 12px;
                font-family: Consolas, "Courier New", monospace; }

QSlider#FSSlider::groove:horizontal {
    background: rgba(255,255,255,60); height: 4px; border-radius: 2px;
}
QSlider#FSSlider::sub-page:horizontal {
    background: #00b578; border-radius: 2px;
}
QSlider#FSSlider::handle:horizontal {
    background: #ffffff; width: 13px; margin: -5px 0;
    border-radius: 7px; border: none;
}
QSlider#FSSlider::handle:horizontal:hover { background: #00b578; }
"""


class EmptyPage(QWidget):
    import_requested = Signal()

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addStretch()

        icon = QLabel("📚")
        icon.setObjectName("EmptyIcon")
        icon.setAlignment(Qt.AlignCenter)

        title = QLabel("还没有课程")
        title.setObjectName("EmptyTitle")
        title.setAlignment(Qt.AlignCenter)

        desc = QLabel("导入老师发给你的课程包（.json），即可开始学习")
        desc.setObjectName("EmptyDesc")
        desc.setAlignment(Qt.AlignCenter)

        btn = QPushButton("＋  导入课程包")
        btn.setObjectName("Primary")
        btn.setMinimumHeight(46)
        btn.setFixedWidth(200)
        btn.clicked.connect(self.import_requested.emit)

        lay.addWidget(icon)
        lay.addSpacing(8)
        lay.addWidget(title)
        lay.addSpacing(6)
        lay.addWidget(desc)
        lay.addSpacing(24)
        lay.addWidget(btn, 0, Qt.AlignHCenter)
        lay.addStretch()


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("GreatClass · 学习中心")
        self.resize(1240, 840)
        self.setMinimumSize(1000, 700)

        self.work_dir = Path(tempfile.mkdtemp(prefix="gc_student_"))
        self.loader = None
        self.current_json = ""
        self._seeking = False
        self._fs_seeking = False
        self.lessons = []
        self.current_lesson_index = -1

        self._fullscreen = False
        self.fs_win = None
        self._video_orig_parent = None
        self._video_orig_layout = None
        self.fs_slider = None
        self.fs_time = None
        self.fs_play_btn = None

        self._build_ui()
        self._build_player()
        self._shortcuts()

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_topbar())

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(28, 22, 28, 22)
        body_lay.setSpacing(14)

        self.stack = QStackedWidget()
        self.empty_page = EmptyPage()
        self.empty_page.import_requested.connect(self.pick_course)
        self.course_page = self._build_course_page()
        self.stack.addWidget(self.empty_page)
        self.stack.addWidget(self.course_page)
        body_lay.addWidget(self.stack, 1)

        self.status = QLabel("就绪")
        self.status.setObjectName("SubTitle")
        body_lay.addWidget(self.status)

        outer.addWidget(body, 1)

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(58)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(28, 0, 28, 0)
        lay.setSpacing(12)

        logo = QLabel("GreatClass")
        logo.setObjectName("Logo")

        slogan = QLabel("学习中心")
        slogan.setObjectName("TopSlogan")
        slogan.setContentsMargins(6, 4, 0, 0)

        self.btn_import = QPushButton("＋  导入课程包")
        self.btn_import.setObjectName("Primary")
        self.btn_import.clicked.connect(self.pick_course)

        self.btn_close = QPushButton("关闭课程")
        self.btn_close.setObjectName("Ghost")
        self.btn_close.clicked.connect(self.close_course)
        self.btn_close.setEnabled(False)

        lay.addWidget(logo)
        lay.addWidget(slogan)
        lay.addStretch()
        lay.addWidget(self.btn_close)
        lay.addWidget(self.btn_import)

        return bar

    def _build_course_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(16)

        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(self._build_video_card(), 7)
        row.addWidget(self._build_side_card(), 3)
        v.addLayout(row, 1)

        return page

    def _build_video_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        video_frame = QFrame()
        video_frame.setObjectName("VideoFrame")
        vf = QVBoxLayout(video_frame)
        vf.setContentsMargins(0, 0, 0, 0)

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(380)
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_widget.setCursor(Qt.PointingHandCursor)
        self.video_widget.installEventFilter(self)
        vf.addWidget(self.video_widget)
        v.addWidget(video_frame, 1)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        v.addWidget(self.slider)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(12)

        self.btn_play = QPushButton("▶")
        self.btn_play.setObjectName("PlayBtn")
        self.btn_play.clicked.connect(self.toggle_play)

        self.btn_back = QPushButton("−10")
        self.btn_back.setObjectName("SmallRound")
        self.btn_back.clicked.connect(lambda: self._seek_rel(-10000))

        self.btn_fwd = QPushButton("+10")
        self.btn_fwd.setObjectName("SmallRound")
        self.btn_fwd.clicked.connect(lambda: self._seek_rel(10000))

        self.lb_time = QLabel("00:00 / 00:00")
        self.lb_time.setObjectName("MetaText")

        self.lb_now_playing = QLabel("")
        self.lb_now_playing.setObjectName("MetaText")

        self.btn_fs = QPushButton("⛶")
        self.btn_fs.setObjectName("SmallRound")
        self.btn_fs.setToolTip("全屏播放 (F)")
        self.btn_fs.clicked.connect(self.enter_fullscreen)

        vol_icon = QLabel("🔊")
        vol_icon.setObjectName("MetaText")

        self.vol = QSlider(Qt.Horizontal)
        self.vol.setObjectName("Volume")
        self.vol.setRange(0, 100)
        self.vol.setValue(80)
        self.vol.setFixedWidth(100)
        self.vol.valueChanged.connect(self._on_volume)

        ctrl.addWidget(self.btn_play)
        ctrl.addWidget(self.btn_back)
        ctrl.addWidget(self.btn_fwd)
        ctrl.addSpacing(8)
        ctrl.addWidget(self.lb_time)
        ctrl.addSpacing(8)
        ctrl.addWidget(self.lb_now_playing, 1)
        ctrl.addWidget(vol_icon)
        ctrl.addWidget(self.vol)
        ctrl.addWidget(self.btn_fs)
        v.addLayout(ctrl)

        return card

    def _build_side_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        card.setMinimumWidth(340)
        card.setMaximumWidth(400)
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(12)

        self.lb_cover = QLabel()
        self.lb_cover.setFixedHeight(150)
        self.lb_cover.setAlignment(Qt.AlignCenter)
        self.lb_cover.setStyleSheet(
            "background: #000000; border-radius: 8px; color: #484f58;"
        )
        v.addWidget(self.lb_cover)

        self.lb_title = QLabel("课程标题")
        self.lb_title.setObjectName("CourseTitle")
        self.lb_title.setWordWrap(True)
        v.addWidget(self.lb_title)

        tag_row = QHBoxLayout()
        tag_row.setSpacing(8)
        self.lb_teacher = QLabel("讲师")
        self.lb_teacher.setObjectName("TeacherTag")
        self.lb_meta = QLabel("")
        self.lb_meta.setObjectName("MetaText")
        tag_row.addWidget(self.lb_teacher)
        tag_row.addWidget(self.lb_meta)
        tag_row.addStretch()
        v.addLayout(tag_row)

        self.lb_desc = QLabel("")
        self.lb_desc.setObjectName("DescText")
        self.lb_desc.setWordWrap(True)
        self.lb_desc.setMaximumHeight(46)
        v.addWidget(self.lb_desc)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background: #ebedf0; max-height: 1px; border: none;")
        v.addWidget(line)

        dir_header = QHBoxLayout()
        dir_title = QLabel("课程目录")
        dir_title.setObjectName("CardTitle")
        self.lb_lesson_count = QLabel("")
        self.lb_lesson_count.setObjectName("MetaText")
        dir_header.addWidget(dir_title)
        dir_header.addStretch()
        dir_header.addWidget(self.lb_lesson_count)
        v.addLayout(dir_header)

        self.lesson_list = QListWidget()
        self.lesson_list.setObjectName("LessonList")
        self.lesson_list.currentRowChanged.connect(self._on_lesson_row_changed)
        v.addWidget(self.lesson_list, 1)

        return card

    def _build_player(self):
        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.audio.setVolume(0.8)

        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_widget)

        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.errorOccurred.connect(self._on_error)
        self.player.mediaStatusChanged.connect(self._on_media_status)

    def _shortcuts(self):
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self.toggle_play)
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=lambda: self._seek_rel(-5000))
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=lambda: self._seek_rel(5000))
        QShortcut(QKeySequence(Qt.Key_F), self, activated=self.enter_fullscreen)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.exit_fullscreen)

    def eventFilter(self, obj, event):
        if obj is self.video_widget and event.type() == QEvent.MouseButtonDblClick:
            if self._fullscreen:
                self.exit_fullscreen()
            else:
                self.enter_fullscreen()
            return True
        return super().eventFilter(obj, event)

    # --------------------------------------------------------
    # 全屏相关
    # --------------------------------------------------------
    def enter_fullscreen(self):
        if self._fullscreen:
            return
        if self.stack.currentWidget() is not self.course_page:
            return

        self._fullscreen = True

        self._video_orig_parent = self.video_widget.parentWidget()
        self._video_orig_layout = self._video_orig_parent.layout() if self._video_orig_parent else None

        self.fs_win = QWidget()
        self.fs_win.setObjectName("FSRoot")
        self.fs_win.setStyleSheet(FS_QSS)
        self.fs_win.setWindowTitle("GreatClass · 全屏播放")
        self.fs_win.setWindowFlag(Qt.Window)
        self.fs_win.setWindowFlag(Qt.FramelessWindowHint)
        self.fs_win.setFocusPolicy(Qt.StrongFocus)

        fs_root = QVBoxLayout(self.fs_win)
        fs_root.setContentsMargins(0, 0, 0, 0)
        fs_root.setSpacing(0)

        video_container = QWidget()
        video_container.setStyleSheet("background: #000000;")
        vc = QVBoxLayout(video_container)
        vc.setContentsMargins(0, 0, 0, 0)
        vc.setSpacing(0)

        self.video_widget.setParent(video_container)
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_widget.setCursor(Qt.ArrowCursor)
        self.video_widget.show()
        vc.addWidget(self.video_widget)

        fs_root.addWidget(video_container, 1)
        fs_root.addWidget(self._build_fs_controls())

        QShortcut(QKeySequence(Qt.Key_Escape), self.fs_win, activated=self.exit_fullscreen)
        QShortcut(QKeySequence(Qt.Key_F), self.fs_win, activated=self.exit_fullscreen)
        QShortcut(QKeySequence(Qt.Key_Space), self.fs_win, activated=self.toggle_play)
        QShortcut(QKeySequence(Qt.Key_Left), self.fs_win, activated=lambda: self._seek_rel(-5000))
        QShortcut(QKeySequence(Qt.Key_Right), self.fs_win, activated=lambda: self._seek_rel(5000))

        self.fs_win.showFullScreen()
        self.fs_win.activateWindow()
        self.fs_win.setFocus()

        # 同步一次状态
        self.fs_slider.setRange(0, self.player.duration())
        self.fs_slider.setValue(self.player.position())
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.fs_play_btn.setText("❚❚")
        else:
            self.fs_play_btn.setText("▶")
        self.fs_time.setText(
            f"{fmt_time(self.player.position())} / {fmt_time(self.player.duration())}"
        )

    def exit_fullscreen(self):
        if not self._fullscreen:
            return

        self._fullscreen = False

        if self._video_orig_parent and self._video_orig_layout:
            self.video_widget.setParent(self._video_orig_parent)
            self._video_orig_layout.addWidget(self.video_widget)
            self.video_widget.setCursor(Qt.PointingHandCursor)
            self.video_widget.show()

        if self.fs_win:
            self.fs_win.close()
            self.fs_win.deleteLater()
            self.fs_win = None

        self.fs_slider = None
        self.fs_time = None
        self.fs_play_btn = None

        # 重新挂载视频输出，避免部分平台黑屏
        self.player.setVideoOutput(self.video_widget)
        self.activateWindow()

    def _build_fs_controls(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("FSBar")
        bar.setFixedHeight(64)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 8, 20, 8)
        lay.setSpacing(14)

        self.fs_play_btn = QPushButton("▶")
        self.fs_play_btn.setObjectName("FSPlayBtn")
        self.fs_play_btn.clicked.connect(self.toggle_play)

        self.fs_slider = QSlider(Qt.Horizontal)
        self.fs_slider.setObjectName("FSSlider")
        self.fs_slider.setRange(0, 0)
        self.fs_slider.sliderPressed.connect(self._on_fs_slider_pressed)
        self.fs_slider.sliderReleased.connect(self._on_fs_slider_released)
        self.fs_slider.sliderMoved.connect(self._on_fs_slider_moved)

        self.fs_time = QLabel("00:00 / 00:00")
        self.fs_time.setObjectName("FSTime")
        self.fs_time.setMinimumWidth(110)
        self.fs_time.setAlignment(Qt.AlignCenter)

        btn_exit = QPushButton("✕")
        btn_exit.setObjectName("FSBtn")
        btn_exit.setToolTip("退出全屏 (Esc)")
        btn_exit.clicked.connect(self.exit_fullscreen)

        lay.addWidget(self.fs_play_btn)
        lay.addWidget(self.fs_slider, 1)
        lay.addWidget(self.fs_time)
        lay.addWidget(btn_exit)

        return bar

    def _on_fs_slider_pressed(self):
        self._fs_seeking = True

    def _on_fs_slider_moved(self, value: int):
        dur = self.player.duration()
        self.fs_time.setText(f"{fmt_time(value)} / {fmt_time(dur)}")

    def _on_fs_slider_released(self):
        self._fs_seeking = False
        self.player.setPosition(self.fs_slider.value())

    # --------------------------------------------------------
    # 播放相关
    # --------------------------------------------------------
    def _seek_rel(self, delta: int):
        if self.stack.currentWidget() is not self.course_page:
            return
        dur = self.player.duration()
        pos = max(0, min(dur, self.player.position() + delta))
        self.player.setPosition(pos)

    def pick_course(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择课程包", "",
            "GreatClass 课程包 (*.json);;所有文件 (*.*)"
        )
        if not path:
            return
        self.current_json = path
        self._load_course(path)

    def _load_course(self, path: str):
        self._reset_player()
        self.lessons = []
        self.current_lesson_index = -1
        self.lesson_list.clear()

        self.btn_import.setEnabled(False)
        self.btn_close.setEnabled(False)
        self.status.setText("正在加载课程包…")

        self.loader = CourseLoader(path, self.work_dir)
        self.loader.progress.connect(lambda p, t: self.status.setText(t))
        self.loader.loaded.connect(self._on_loaded)
        self.loader.failed.connect(self._on_failed)
        self.loader.start()

    def _on_loaded(self, meta: dict, lessons: list):
        self.lessons = lessons

        self.lb_title.setText(meta["title"])
        self.lb_teacher.setText(meta["teacher"])

        bits = []
        if meta.get("created_at"):
            bits.append(meta["created_at"].replace("T", " ")[:16])
        self.lb_meta.setText("   ·   ".join(bits))

        desc = meta.get("description") or "暂无课程介绍"
        self.lb_desc.setText(desc)

        if meta.get("cover_path") and Path(meta["cover_path"]).exists():
            pix = QPixmap(meta["cover_path"])
            if not pix.isNull():
                self.lb_cover.setPixmap(
                    pix.scaled(self.lb_cover.size(),
                               Qt.KeepAspectRatioByExpanding,
                               Qt.SmoothTransformation)
                )

        self.lb_lesson_count.setText(f"共 {len(lessons)} 集")

        for i, l in enumerate(lessons):
            title = l["title"]
            dur = l.get("duration", 0)
            text = f"{i+1:02d}   {title}"
            if dur > 0:
                text += f"    {fmt_time(int(dur*1000))}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, i)
            item.setSizeHint(QSize(0, 42))
            self.lesson_list.addItem(item)

        self.stack.setCurrentWidget(self.course_page)
        self.btn_import.setEnabled(True)
        self.btn_close.setEnabled(True)

        if lessons:
            self.lesson_list.setCurrentRow(0)

        self.status.setText(f"课程已就绪 · 共 {len(lessons)} 集")

    def _on_failed(self, msg: str):
        self.btn_import.setEnabled(True)
        self.status.setText("加载失败")
        QMessageBox.critical(self, "加载失败", f"无法打开课程包：\n\n{msg}")

    def _on_lesson_row_changed(self, row: int):
        if row < 0 or row >= len(self.lessons):
            return
        if row == self.current_lesson_index:
            return
        self.current_lesson_index = row
        lesson = self.lessons[row]

        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(lesson["video_path"]))
        self.slider.setRange(0, 0)
        self.slider.setValue(0)
        self.lb_time.setText("00:00 / 00:00")

        self.lb_now_playing.setText(f"正在播放 · {lesson['title']}")

        if lesson.get("cover_path") and Path(lesson["cover_path"]).exists():
            pix = QPixmap(lesson["cover_path"])
            if not pix.isNull():
                self.lb_cover.setPixmap(
                    pix.scaled(self.lb_cover.size(),
                               Qt.KeepAspectRatioByExpanding,
                               Qt.SmoothTransformation)
                )

        self.player.play()

    def close_course(self):
        self._reset_player()
        self.lessons = []
        self.current_lesson_index = -1
        self.lesson_list.clear()
        self.stack.setCurrentWidget(self.empty_page)
        self.btn_close.setEnabled(False)
        self.status.setText("已关闭课程")

    def _reset_player(self):
        if self._fullscreen:
            self.exit_fullscreen()
        try:
            self.player.stop()
            self.player.setSource(QUrl())
        except Exception:
            pass
        self.slider.setRange(0, 0)
        self.slider.setValue(0)
        self.lb_time.setText("00:00 / 00:00")
        self.lb_cover.clear()
        self.lb_now_playing.setText("")

    def toggle_play(self):
        if self.stack.currentWidget() is not self.course_page:
            return
        st = self.player.playbackState()
        if st == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_state(self, state):
        playing = state == QMediaPlayer.PlayingState
        self.btn_play.setText("❚❚" if playing else "▶")
        if self.fs_play_btn:
            self.fs_play_btn.setText("❚❚" if playing else "▶")

    def _on_position(self, pos: int):
        if not self._seeking:
            self.slider.setValue(pos)
        dur = self.player.duration()
        self._update_time_label(pos, dur)

        if self.fs_slider and not self._fs_seeking:
            self.fs_slider.blockSignals(True)
            self.fs_slider.setValue(pos)
            self.fs_slider.blockSignals(False)
        if self.fs_time:
            self.fs_time.setText(f"{fmt_time(pos)} / {fmt_time(dur)}")

    def _on_duration(self, dur: int):
        self.slider.setRange(0, dur)
        self._update_time_label(self.player.position(), dur)
        if self.fs_slider:
            self.fs_slider.setRange(0, dur)

    def _update_time_label(self, pos: int, dur: int):
        self.lb_time.setText(f"{fmt_time(pos)} / {fmt_time(dur)}")

    def _on_slider_pressed(self):
        self._seeking = True

    def _on_slider_moved(self, value: int):
        self._update_time_label(value, self.player.duration())

    def _on_slider_released(self):
        self._seeking = False
        self.player.setPosition(self.slider.value())

    def _on_volume(self, v: int):
        self.audio.setVolume(v / 100.0)

    def _on_error(self, err, msg: str):
        if err == QMediaPlayer.NoError:
            return
        self.status.setText(f"播放错误：{msg}")

    def _on_media_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            nxt = self.current_lesson_index + 1
            if 0 <= nxt < len(self.lessons):
                self.status.setText(
                    f"第 {self.current_lesson_index+1} 集播放完毕 · 即将切到下一集"
                )
                self.lesson_list.setCurrentRow(nxt)
            else:
                self.status.setText("课程已全部播放完毕")

    def closeEvent(self, event):
        if self._fullscreen:
            self.exit_fullscreen()
        try:
            self.player.stop()
        except Exception:
            pass
        shutil.rmtree(self.work_dir, ignore_errors=True)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("GreatClass Student")

    font = QFont("Microsoft YaHei UI", 9)
    if not font.exactMatch():
        font = QFont("PingFang SC", 9)
    app.setFont(font)

    app.setStyleSheet(QSS)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
