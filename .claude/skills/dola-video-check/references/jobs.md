# 作业JSON

中文路径与参数通过UTF-8 JSON传递，不在命令行放密钥。两个脚本均以`--job 作业.json`运行；可直接使用维护源脚本或Codex安装副本。路径均用绝对路径。

## 检查

`audit.py`作业：

```json
{
  "project_root": ".",
  "output_dir": "./工具/运行垃圾/tasks/dola-check-本批",
  "units": [
    {"unit": 1, "video": "./stories/本集/分镜-30秒/成片/实际视频.mp4", "prompt_file": "./stories/本集/分镜-30秒/分镜提示词.md"}
  ]
}
```

`prompt_file`可以是完整分镜，也可以是原请求独立prompt.txt。脚本按单元号提取对应代码块内对白；每条只做一次全长识别，不分段、不生成截图，按视频与提示词摘要隔离缓存，已有整段结果直接复用。`video`直接指向已有视频，不为检查或交付复制视频。它只提供对白证据，由执行者判断台词是否基本相同，不自动判废。`--dry-run`只核对输入和依赖，不转录、不提交。

本机已有依赖：`工具/运行垃圾/deps/asr-sherpa`、`工具/运行垃圾/deps/sensevoice/model.int8.onnx`与`tokens.txt`；FFmpeg沿用项目剪映环境或现有可执行文件。缺少时报告实际缺项，不默默安装或下载新模型。

## 原样重提与查询

`same.py`作业：

```json
{
  "operation": "submit",
  "project_root": ".",
  "source_task_dir": "./工具/运行垃圾/tasks/原批次/U01",
  "task_dir": "./工具/运行垃圾/tasks/本次重试/U01/attempt-1",
  "unit": 1,
  "retry_number": 1,
  "max_retries": 3,
  "channel_id": "dola-pool",
  "channel_revision": "原配置版本",
  "output_dir": "./stories/本集/分镜-30秒/成片"
}
```

`source_task_dir/request.json`必须是已存在的原Dola请求，支持项目CLI的顶层字段或工作台的`fields`结构，并保留按顺序的`references`路径与SHA-256。`retry_number`填写**本单元累计次数**，从原账本统计，不能按新目录重新从1计数。原输入发生变化就停止，不把改后的东西称为原样重提。

调用`submit`前，执行者应已核对原任务与本轮废片证据。`same.py`不代替这个内容判定。用`--dry-run`核对请求复用，不查网络、不计费。

新目录已有创建账本时`submit`不再POST，返回原状态。将`operation`改成`poll`查询该目录已有任务，完成后下载和技术核对；没有任务号则停止对账。同一远端任务只保留一份必要的视频，后续检查和交付引用已有路径，不重复下载或复制。自动化每20分钟查询一次，每次只调用一次，不使用长期`--watch`进程。仅新片对白核对通过后，执行者更新对应单元的最新版本映射。

`channel_revision`优先填原配置版本；未填时使用指定渠道的当前版本，新提交会记录版本及密钥摘要，后续查询按原版本恢复。统一配置=`<个人工作台>/runtime/storyboard/channels.json`，只支持当前项目单Key的Dola渠道。原Key或对应配置不可用时停止，不改已有绑定。

累计Dola重提达到上限仍无可用结果时，按`SKILL.md`的“重提上限后转Wan”使用对应Wan渠道的已有执行器；`same.py`仍只负责Dola原样提交与查询，不用于提交Wan。Wan任务和Dola账本分别记录，Wan成片不送入`audit.py`，下载后按免对白检查交付。
