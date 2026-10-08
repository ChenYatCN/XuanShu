# XYZ 同步异常隔离修复（2026-10-08）

用户反馈：XYZ 坐标同步时 `ExceptionalTimeout: Timed out waiting for coro should_update` 冒泡到 `handle_gui`，被 GUI 主循环判定为致命错误，导致玄枢退出并停止所有快捷键。

## 修改

- `XuanShu.py:xyz_sync` 将每个目标客户端的区域/坐标读取和 `teleport` 包在单客户端异常隔离中；传送超时、内存读取失败、客户端关闭等普通异常只记录并跳过该客户端，继续处理后续已选客户端。
- 传送后的 A/D 转向改为每个客户端独立处理；某个客户端转向失败不会取消其他客户端的转向。
- `asyncio.CancelledError` 不被普通异常捕获，仍可正常取消整个同步操作。
- 未修改 `navmap_teleport`、地牢同步、自动任务或 GUI 主循环的全局退出策略。

## 验证

`tests.test_xyz_sync` 与既有 XYZ/区域安全测试共 16 项通过：覆盖 `ExceptionalTimeout`、源/目标内存读取失败、单客户端关闭、转向失败、取消清理、下一次同步恢复、GUI 连续命令，以及补药/恢复/加载/区域变化保护。源码编译检查和 `git diff --check` 通过（仅有 LF/CRLF 提示）。没有打包、安装或游戏内验证。
