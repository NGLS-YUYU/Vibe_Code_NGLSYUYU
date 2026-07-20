# -*- mode: python ; coding: utf-8 -*-
block_cipher = None

# 要打包成单个 EXE
a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),  # HTML 模板文件夹
        ('static', 'static'),        # 静态资源（icon.png / page.png）
    ],
    hiddenimports=[
        'flask',
        'flask_sqlalchemy',
        'flask_login',
        'sqlalchemy',
        'sqlalchemy.sql.default_comparator',
        'werkzeug',
        'pandas',
        'openpyxl',
        'waitress',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='仓储管理系统',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # True = 保留终端窗口（用户可看到启动日志 + 关闭即停止）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',  # 可改为 'icon.ico' 自定义图标
)