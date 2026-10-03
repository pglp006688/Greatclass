# GreatClass

[English](./README.md)

>需要 `ffmpeg.exe` 在 `bin` 文件夹
## 关于录制
自行选择，我没有强迫，可以是OBS Studios或其他。但我用[https://github.com/Zhischooler/Greatclass-Recorder](https://github.com/Zhischooler/Greatclass-Recorder)
##
一个用于录制在线课程的软件 离线录制讲座工具。老师可以上传视频，系统会自动压缩到720p并打包成一个JSON课程文件；学生只需导入JSON就可以开始学习，支持切换章节、全屏播放，以及带有继续播放功能的控制栏。
### 全程不依赖服务器，一个 JSON 文件即一门课。
- **教师端**：多集视频一次导入，自动 ffmpeg 压缩 720p，Base64 内嵌，一键生成 `.gclass.json`
- **学生端**：导入 JSON 即看，自动解码视频到临时目录，播放完自动清理
- **多集目录**：右侧课程目录可自由切换，播完自动跳下一集
- **全屏播放**：快捷键 `F` / 双击画面 / 点 `⛶` 进入，`Esc` 退出，控制条同步
- **纯离线**：视频、封面全部 Base64 内嵌在 JSON 里，微信、QQ、U盘都能传
