# 发布说明（维护者）

这个项目同时存在两份东西，别混淆：

| 名称 | 位置 | 用途 |
| --- | --- | --- |
| **正在用版** | `dist\szu-netlogin\` | 你本机日常使用的程序（含 `_internal`） |
| **上传版（源码）** | 仓库根目录里的源码文件 | 推到 GitHub 的仓库内容 |
| **上传版（发布包）** | `dist\szu-netlogin-v1.2.0-win64.zip` | 传到 GitHub Releases 的附件 |

## 一、上传到 GitHub 仓库的内容

只提交源码和文档（`.gitignore` 已排除构建产物与本地凭据）：

```
.gitattributes  .gitignore  LICENSE  README.md  CHANGELOG.md  pyproject.toml
app_main.py  autologin.py  gui.py  dpapi.py  make_icon.py
assets/icon.ico
campusnet/        # 上游 MIT 引擎（保留 LICENSE 与版权声明）
docs/             # 截图、图标预览、协议文档、发布说明
tests/            # 上游测试
setup.bat  status.bat  install_startup.bat  uninstall_startup.bat
图形界面.bat  校园网助手.bat
```

**绝对不要提交：**

```
dist/  build/  *.spec  __pycache__/  _pytmp/
%APPDATA%\campusnet\config.json
%APPDATA%\CampusNetAutoLogin\password.dat
%APPDATA%\CampusNetAutoLogin\autologin.log
```

其中 `dist/` 已被 `.gitignore` 第 42 行排除。

## 二、发布包怎么构建

```console
python -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --icon assets\icon.ico --add-data "assets\icon.ico;assets" ^
  --name szu-netlogin app_main.py
```

构建后把下面三个文件放进 `dist\szu-netlogin\`，再整体压缩：

- `使用说明.txt`
- `卸载.bat`（来自 `assets/uninstall.bat`，必须 CRLF + ASCII）
- 目录里必须包含 `_internal\`，不能只取 exe

压缩包命名：`szu-netlogin-v<版本>-win64.zip`，上传到 GitHub Releases。

## 三、改版本号要同步的地方

1. `gui.py` 里的 `VERSION`
2. 压缩包文件名
3. `README.md` 的下载链接
4. `使用说明.txt` 的标题
5. 本文档里的 zip 名称

## 四、上传前安全自检

- [ ] 全仓库搜索真实学号：0 命中（含 exe 二进制的 UTF-8 / UTF-16 扫描）
- [ ] 无 `config.json`、`password.dat`、`*.log`
- [ ] `git status` 里看不到 `dist/`
- [ ] `docs/screenshot.png` 使用虚构账号（2024000000 / 10.20.30.40）
- [ ] `LICENSE` 保留上游 MIT 版权声明
- [ ] 图标为原创设计，未使用学校官方校徽素材

## 五、卸载功能说明

卸载由发布包里的 `卸载.bat` 完成（不依赖程序本身）：

1. 结束 `szu-netlogin.exe`
2. 删除注册表自启项 `CampusNetAutoLogin`
3. 删除 `%APPDATA%\campusnet` 与 `%APPDATA%\CampusNetAutoLogin`
4. 把自己复制到临时目录，再从临时目录删除整个程序文件夹

程序界面里的「清除账号数据」只负责前三步（自启、配置、密码），
删除程序文件夹请双击 `卸载.bat`。
