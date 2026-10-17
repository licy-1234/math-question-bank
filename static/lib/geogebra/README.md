# GeoGebra 网页引擎本地镜像（引擎文件不入库）

本目录用于存放 GeoGebra 网页引擎（web3d）的**本地镜像**，供 `static/js/ggb.js` 的
`PREFER_LOCAL_ENGINE` 本地优先路径使用。

## 重要：引擎文件不进 git

公开仓库等于公开再分发，而 GeoGebra 引擎的再分发许可尚未确认。因此：

- 本目录下除本 `README.md` 外的所有文件（引擎本体、`ENGINE-VERSION.json` 等）都被
  `.gitignore` 忽略，**不得提交**；
- 引擎镜像由本地脚本生成，不进版本控制：

  ```bash
  python tools/geogebra/mirror_engine.py            # 下载 + 校验 + 写版本戳
  python tools/geogebra/mirror_engine.py --check    # 只校验完整性，不发网络
  python tools/geogebra/mirror_engine.py --dry-run  # 预览将下载的文件与预计体积
  ```

- 发布时由非 git 通道复制到发布副本：

  ```bash
  python tools/release/stage_engine.py --dest <发布副本根目录>
  ```

## 目录结构

```text
static/lib/geogebra/
├── README.md            # 本说明（唯一允许入库的占位文件）
├── ENGINE-VERSION.json  # 版本戳（不入库）
└── 5.4.932.1/
    ├── deployggb.js
    ├── web3d/           # web3d.nocache.js、GWT 强名 cache.js、deferredjs、properties
    └── css/             # bundles / keyboard-styles / fonts / greek-font
```

## 默认开关状态

`ggb.js` 中 `PREFER_LOCAL_ENGINE = false`（阶段一默认关闭），关闭时行为与既有 CDN
路径逐字节一致。确认再分发许可后，再打开该开关并补齐运行时存在性探测（阶段二）。
