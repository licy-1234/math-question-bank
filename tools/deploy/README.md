# 部署与迁移工具（防"版本回退"）

## 为什么会"看起来回退"——两层原因，缺一不可

**第一层：程序有两份，用户跑的不是 Git 仓库那一份。**

仓库在这里（有提交历史、有最新代码）：

```
C:\Users\虫鱼\WorkBuddy\2026-10-07-23-33-22\math-question-bank
```

用户实际双击启动的是桌面上的**便携包副本**（**不是** Git 仓库）：

```
C:\Users\虫鱼\Desktop\MathBank-Windows-x64
```

它靠"解压新版 ZIP 覆盖目录"升级（`覆盖升级说明.txt`）。只要没做过覆盖升级，
它就一直停在解压那天的代码上——仓库里提交得再勤，它一动不动。
2026-10-08 那次就是这样：副本停在提交 `1251557`（10-07 10:05），
而 01:14 / 02:35 / 03:18 三个优化提交比它新，所以页面显示的是"未修改的原始题库"。

**第二层：数据库 schema 没跟着升级。**

标签体系要用到 `question_tags` 表（schema v11）。副本里的库长期停在 v10，
即使代码换了新的，标签也**无处可存**，页面自然还是老样子。
另外 `.gitignore:13` 有 `*.db`，数据库不受版本控制，无法靠 `git checkout` 找回。

## 四个脚本，按顺序用

都用仓库里的解释器即可（`tools/` 下的脚本不依赖副本环境），
只有 `migrate_db.py` 必须用副本自带的解释器。

### 1. 先体检：`check_drift.py`

```
python tools/deploy/check_drift.py --repo <仓库> --dest <副本>
```

回答"用户跑的那份和仓库是不是同一份"。退出码 0 = 无漂移，1 = 有漂移。
会列出落后哪几个提交（带时间）、哪些文件不一致、数据库 schema 是否落后。

**建议：每次改完代码准备给老师用之前，先跑一遍。**

### 2. 同步代码：`sync_release.py`

```
python tools/deploy/sync_release.py --repo <仓库> --dest <副本> --since <副本当前提交> --dry-run
python tools/deploy/sync_release.py --repo <仓库> --dest <副本> --since <副本当前提交>
```

- `--since` 只同步该提交之后变化过的文件（最小增量，推荐）。省略则按目录全量同步。
- `--dry-run` 只看清单不动手，**第一次务必先跑它**。
- 被覆盖的旧文件先备份到 `<副本>/_predeploy_backup_<时间戳>/`。
- 硬性保护，永远不碰：`.env`、`*.db` / `-wal` / `-shm`、`data_backup/`、
  `python/`、`*.zip`、`RELEASE-MANIFEST.json`。
- `tests/` 与 `tools/` 属于开发资产，不会进用户副本。
- 同步完会写 `<副本>/DEPLOY-INFO.json`：来源提交、时间、每个文件的 sha256。

### 3. 迁移数据库：`migrate_db.py`

```
<副本>\python\python.exe tools/deploy/migrate_db.py --project-root <副本>
<副本>\python\python.exe tools/deploy/migrate_db.py --project-root <副本> --check-only
```

- 内部调用仓库自带的 `mathbank.db_migrations.migrate_database()`，
  它会**自动生成迁移前一致性快照**（`data_backup/schema_snapshots/*.db` + `.sha256`）。
- 迁移前后各做一次指纹快照并逐项比对，**题目数或正文指纹变了就直接判 FAIL**。
- 只建表 + 从旧"册/章/节"回填 chapter 标签，**不改写任何题目主体内容**，可重复执行。

### 4. 数据指纹：`snapshot_db.py`

```
python tools/deploy/snapshot_db.py <db路径> [输出json]
```

任何动数据库的操作前后各跑一次，比对 `count` 和 `fingerprint` 就能证明题没丢没改。

附：`match_deployed_commit.py` 用来反查"这份副本到底等于哪个提交"，
按文件字节逐个比对仓库历史，能给出"副本 == 提交 X"这种硬证据。

## 防再次回退的三条规矩

1. **改完代码必须走一遍 `check_drift.py`**，退出码必须是 0，再交付给老师。
   这一条能挡住 90% 的"我改了但没生效"。
2. **不要绕过 `sync_release.py` 手动拷文件**。手动拷最容易漏掉新文件
   （这次就漏了 `mathbank/tags.py`、`A2019.json`、`tag_schema.json`、`static/js/tags.js` 四个），
   漏一个就整块功能消失。
3. **动数据库前先 `snapshot_db.py`**，动完再跑一次比对指纹。

## 这次修复留下的可验证痕迹

| 项目 | 位置 |
| --- | --- |
| 代码部署前备份（被覆盖的 14 个旧文件） | `<副本>\_predeploy_backup_20261008_194533\` |
| 数据库迁移前一致性快照 | `<副本>\data_backup\schema_snapshots\math_question_bank.schema-v10-to-v11.*.db` |
| 部署版本戳 | `<副本>\DEPLOY-INFO.json` |
| 迁移前题目指纹 | 14 道题，sha256 `72576346572e4865adb56c4f075b1e80a584abbe5381ee72e07609ee6d887a2c` |
