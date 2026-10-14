' 养老监护系统 - 开机自启（静默运行 autostart.bat，不弹黑窗）
Set ws = CreateObject("Wscript.Shell")
ws.CurrentDirectory = "E:\ros_vision"
ws.Run "E:\ros_vision\autostart.bat", 0, False
