# 五栏日报

每天早上 8:00（新加坡时间）出一期的个人新闻简报，五个栏目：

| 栏目 | 关注什么 |
|---|---|
| 前沿 AI | AI 公司动态、重大科研进展 |
| 游戏 | 新游发售、新作公布、好玩的东西 |
| 政治与经济 | 各国新出台的政策、法规与经济动向 |
| 商业与产品 | 值得关注的新公司和有意思的新产品 |
| 热议 | 正在快速升温的话题、讨论与争论 |

- **网站**：https://tyr1105.github.io/daily-news/
- **安卓 App**：[下载最新安装包](https://github.com/tyr1105/daily-news/releases/latest/download/wulan-daily.apk)。打开就是网站，下拉刷新；每天新一期出来后会发一条通知。

## 怎么运作

1. 每天早上，一个定时运行的 Claude 会话按 [EDITOR_GUIDE.md](EDITOR_GUIDE.md) 检索公开报道、交叉核对，写成中文。
2. `scripts/publish_edition.py` 检查格式，把这一期写进 `docs/data/<日期>.json`，并更新往期目录 `docs/data/index.json`。
3. 推送到 `main` 后，GitHub Pages 从 `docs/` 发布网站。
4. App 每 15 分钟看一次 `data/index.json`，发现新一期就发通知。

想改关注点：直接编辑 [EDITOR_GUIDE.md](EDITOR_GUIDE.md) 里的“栏目设置”。

## 目录

```
docs/            网站（GitHub Pages）
  index.html     由 src/page.html 生成：python3 scripts/build_site.py
  data/          每期一个 JSON，外加目录 index.json
src/page.html    页面源文件（也作为 claude.ai 版发布）
scripts/         生成网站、检查并发布每期内容
android/         安卓 App（WebView + 新一期通知），推送后由 GitHub Actions 自动构建
```

## 关于 App 签名

`android/app/wulan.keystore` 是这个个人 App 专用的签名文件，放在仓库里是为了让 GitHub Actions 每次构建出的新版本都能直接覆盖安装。它只用来签这个 App。

内容由 AI 整理，难免有疏漏或错误，重要信息请看原文来源。
