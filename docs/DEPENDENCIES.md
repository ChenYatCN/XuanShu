# 项目依赖来源

## 当前运行与打包

- 主环境：`.venv`。启动 BAT 和 `XuanShu.spec` 优先使用 `.venv/Lib/site-packages`。
- 打包 BAT 不自动更新 wizwalker，不执行 uv sync；打包使用当前已安装版本。
- 本次整理不改安装包、启动 BAT、打包 BAT、spec 或 pyproject.toml。

## 保留目录及用途

| 位置 | 用途 | 注意事项 |
| --- | --- | --- |
| `.venv` | 当前运行、测试、打包的依赖环境 | 不当作缓存删除 |
| `libs/wizwalker` | workspace 源码及本地 Hook 定制 | 不代表当前实际导入源 |
| `libs/wizsprinter` | workspace 源码及打包使用的战斗/分辨率兼容模块 | spec及打包校验仍引用，不可整体移走 |
| `libs/wizlaunch`、`libs/kinif` | pyproject 声明的 workspace 源码 | 保留；是否安装以实际模块路径核对 |
| 根目录 `wizwalker` | 旧兼容源码；其中 mouse_handler.py 是现有打包前同步脚本的定制来源 | 不能整包移走；不要直接把它放到依赖搜索路径前面 |
| `packaging` | 定制同步、来源检查和打包校验 | 沿用现有流程 |

## 已归档的闲置依赖

2026-10-22 将以下目录移至项目父目录的 `Back/dependency-cleanup-20261022/`：

- `myenv`：Python 3.12.1旧环境，配置指向旧Deimos目录，当前启动/打包未引用。
- `wheels`：PySimpleGUI 4.60.5旧离线安装包，当前代码和维护脚本未发现引用。

备份保存逐文件SHA256清单 `manifest.json`。两目录含Git已跟踪文件，工作区中的相应删除是此次迁移产生的，尚未提交。

需要撤销时，将备份下的同名目录移回项目根目录；若原路径已重新出现，先核对内容，禁止覆盖。

## 维护约定

- 保持当前 `.venv` 作为打包来源；不把源码副本的版本号误当成安装环境版本。
- 上游更新前核对安装来源提交和本地定制，更新后核对实际导入路径。
- pip 的 Git 安装与 uv workspace 是不同安装来源。执行 uv sync 会按项目 workspace 配置处理环境，不能作为无影响的清理步骤运行。
- 本次仅归档闲置副本，不统一这两种安装来源，不删除仍被引用的兼容源码。