# 原则/清单/规则候选提取 — 张立浩交流第一通电话

> extractor: principle-extractor｜阶段1并行产出之一
> 证据层级标注说明:
> 【原话】= 转写中对方(讲话人2=张立浩)明确说过,给时间戳+引句
> 【原话-转述】= 对方明确说过,但内容是转述第三方团队的经验/数据,非对方亲身实操
> 【原话-非对方】= 转写中确有此话,但发言人是讲话人1(我方/用户团队),不是张立浩本人的经验主张,需注意不要误标为"对方经验"
> 【整理层】= 整理稿(蒸馏稿)的解读/扩展/细化,原话没有说到这个颗粒度,属推断
> 口头数据(转化倍数、播放/销售额比例、限额数字等)一律标注"待验证",不作为确定规则

---

```yaml
- id: p01
  title: 播放问题与转化问题要分开诊断
  type: rule
  source_chapter: "02:18–03:16"
  source_quote: |
    "但你现在问题是播放量上不去,还是流速上不去。"(讲话人2, 02:18)
    "有一个问题你没搞清楚啊……一是品的问题,第二……"(讲话人2, 03:16)
  summary: |
    对方在诊断"不跑量"时,第一步先把"播放/流速起不来"和"转化/成交不好"
    拆成两个独立问题分别提问,不接受笼统的"不行"作为诊断输入。
  tags: [principle, diagnosis, attribution-splitting]
  evidence_tier: 原话
```

```yaml
- id: p02
  title: 商品生命周期决定转化率,不是任何时候都好卖
  type: principle
  source_chapter: "03:20–03:44"
  source_quote: |
    "这个品它是周期的……一开始起量,就是你流速再快它都不怎么转化。但是你到高峰期
    ……它的转化是很高的。但是如果你打到下坡的时候,它的转化就非常低。"(讲话人2)
  summary: |
    同一商品在起量/高峰/下坡三个阶段转化效率完全不同;起量期和下坡期即使流速快
    转化也低,只有高峰期转化才高。选品判断必须先定位当前处于哪个阶段。
  tags: [principle, product-lifecycle, selection]
  evidence_tier: 原话
```

```yaml
- id: p03
  title: 判断品处于下坡期时应及时换品
  type: rule
  source_chapter: "03:44–03:55"
  source_quote: |
    "我这个时候这红烧酱汁相当于是我就在走下坡路的时候进的……打的人还是很多。
    所以我建议你这个品可以换一下,我们都没怎么打这个红烧酱汁。"(讲话人1/讲话人2)
  summary: |
    "很多人在打"不等于"值得继续打";一旦判定品已进入下坡阶段(即使仍有人跟打),
    对方给出的动作是直接换品,而不是留在同一个品上加大投入。
  tags: [principle, product-lifecycle, action-rule]
  evidence_tier: 原话
```

```yaml
- id: p04
  title: 剧情带货素材应固定加入画中画以提升转化
  type: rule
  source_chapter: "04:00–04:24"
  source_quote: |
    "你的素材肯定的没加画中画……你加了画中画试试……我们同样的播放量一条加一条
    不加差了,接近10倍。"(讲话人2)
  summary: |
    对方明确建议剧情带货素材固定加画中画,声称同播放量下加/不加转化差距接近
    十倍。"接近十倍"是口头数据,待验证;但"固定加画中画"这个动作本身是对方
    明确给出的规则。
  tags: [principle, pip, conversion, unverified-data]
  evidence_tier: 原话（数据部分待验证）
```

```yaml
- id: p05
  title: AI洗稿/提炼底层逻辑的提示词容易出现时序倒序,需人工调整
  type: rule
  source_chapter: "07:09–07:29"
  source_quote: |
    "他会把测试的那些剧本的核心的故事链条提炼出来……但是它这个也有点问题,我们
    用了就发现它倒序很多这种到时候你需要自己去调一下。"(讲话人2)
    "我也我之前洗也是经常会出倒序。"(讲话人1)
  summary: |
    双方各自独立确认:用AI提炼底层逻辑链再洗稿的流程,普遍会产出时序颠倒的
    问题,这是已知的、需要人工介入修正的缺陷,不是偶发个案。
  tags: [rule, failure-mode, sequencing, quality-check]
  evidence_tier: 原话（双方互证）
```

```yaml
- id: p06
  title: （非对方经验）智能体审稿经验应固化入库形成可成长系统
  type: principle
  source_chapter: "07:47–08:09"
  source_quote: |
    "我是倾向于……我有审稿经验啊,我往哪方面偏向,我在选题上面给他改,改完了他
    就入库,入库了之后下次就不会犯同样的问题,我是偏向于做一种这种框架。"(讲话人1)
  summary: |
    这是讲话人1(我方/用户团队)自述的方法论主张,不是张立浩的经验或建议——
    张立浩对此只回应"理解啊"未置可否。下游引用时不应把它标注为"对方原则"。
  tags: [principle, agent-system, self-improving, attribution-caution]
  evidence_tier: 原话-非对方
```

```yaml
- id: p07
  title: 二创与洗稿两条路线应并行,不能只做一种
  type: rule
  source_chapter: "08:22–08:39"
  source_quote: |
    "我们现在啊是两条路在走,一个是走就是二创……第二是用这种洗稿,因为我们要
    铺量肯定要走洗稿,你全是纯二创时间那个量也跟不上。"(讲话人2)
  summary: |
    全做高改动二创,产量跟不上;全做低改动洗稿,同质化风险高。对方团队的做法
    是两条路线同时跑,而不是二选一。
  tags: [principle, production-strategy, dual-track]
  evidence_tier: 原话
```

```yaml
- id: p08
  title: 素材回本的口径:成本能被销售额覆盖即算不亏（口头数据待验证）
  type: rule
  source_chapter: "08:39–08:53"
  source_quote: |
    "我们起码一条比如跑1万播放。我们能卖1000块钱,起码制作成本和号钱能回来
    我们是不亏钱的啊。"(讲话人2)
    "我们昨天真的1万播放卖了1000多。"(讲话人2)
  summary: |
    对方给出一个简化的盈亏判断标准:只要单条素材的销售额能覆盖制作成本+账号
    成本,就不算亏钱,不需要播放量特别高。"一万播放卖一千元"是口头数据,
    未提供完整样本,必须待验证,不能直接作为自己的预期基准。
  tags: [rule, unit-economics, unverified-data]
  evidence_tier: 原话（具体数字待验证）
```

```yaml
- id: p09
  title: 本子需要持续产生小额成交才能维持流速,一次性起量后断供会掉量
  type: principle
  source_chapter: "14:00–14:35"
  source_quote: |
    "我们之前前两天上个星期就刚开始做的时候就踩了个坑,就发现这个本子跑完就
    是不跑了。但我们这两天是今天跑一会儿,明天还跑一会儿,它会继续跑。"(讲话人2)
    "那我大概知道了,因为它会很长时间的20 23 10这种流速跑能跑几个小时,但是
    中间没有钱,没有成交,导致流速上不来。"(讲话人1)
  summary: |
    对方团队的实操经验是:素材要"持续小额出单"才能维持流速不掉,一次性冲高
    后如果中间没有成交反馈,流速会停滞或衰减。此处"低转化导致流速衰减"仍是
    未经对照实验验证的因果假设(见 BOOK_OVERVIEW 批判层),提取为原则但保留
    因果强度待验证的标注。
  tags: [principle, velocity, conversion-feedback, unverified-causality]
  evidence_tier: 原话
```

```yaml
- id: p10
  title: 账号违规1-2次后弃用（团队风控惯例，非平台规律）
  type: rule
  source_chapter: "14:35–14:50"
  source_quote: |
    "目前一个账号如果违规了1到2次之后我们绝对不会用的……违规之后这个账号
    肯定会限流的。"(讲话人2)
  summary: |
    这是对方团队自定的风控惯例(表态用词是"我们绝对不会用"),不是平台公开
    规律或实证结论。使用时应明确标注"这是他人团队的风险偏好,不是平台机制
    本身",并建议记录违规类型/时间/前后播放数据再自行决策,而非直接照搬弃用
    阈值。
  tags: [rule, account-risk, team-convention-not-platform-rule]
  evidence_tier: 原话（团队惯例，非平台规律——按任务纪律特别标注）
```

```yaml
- id: p11
  title: 选对标要避开当前扎堆题材,优先找"验证过需求但近期供给低"的旧框架
  type: rule
  source_chapter: "15:01–16:22"
  source_quote: |
    "跑不上去很大的原因就是同质化……我们昨天挣了一个,就是去年的那个真人剧情
    的一个本子,然后改了一下放上去打,那流速很快,直接就打了几十万播放。"
    "不是真人报过了,是这个本子最近没人打啊。"(讲话人2)
  summary: |
    对方特意纠正了一个容易误解的因果:让旧本子重新起量的关键不是"真人剧情
    转AI"这个形式,而是"近期供给低"(没人打)。选对标的完整口径是:题材避开
    当前扎堆区,优先找历史验证过需求、但近期没什么人打的框架。
  tags: [principle, benchmark-selection, blue-ocean, homogenization]
  evidence_tier: 原话
```

```yaml
- id: p12
  title: 提升转化的两个抓手:选品选好 + 避免同质化
  type: maxim
  source_chapter: "16:22–16:41"
  source_quote: |
    "其实这个转化还是比较简单的。第一选品好,第二,你的本子一定跑不上去,
    就同质化。"(讲话人2)
  summary: |
    对方把复杂的运营问题收敛成两句话的箴言式总结,反复在通话中出现
    (16:22 与 20:26 两次重申)。
  tags: [maxim, summary, two-factor]
  evidence_tier: 原话（在通话中重复出现两次）
```

```yaml
- id: p13
  title: "★剧本改编·开头是明确可变项,且应改得比对标更夸张"
  type: principle
  source_chapter: "21:00–21:24"
  source_quote: |
    "首先开头这个东西……我们可能会把这个开头改得更夸张或更怎么样。"(讲话人2)
  summary: |
    这是通话中剧本改编段落(21:00–22:39,用户第一优先方向)明确点名的第一条
    可变项。要点有两层:①开头允许改;②改动方向不是"随意变化"而是"加码"——
    比原对标更夸张。整理稿把这点弱化为诊断清单里的一句"开头是否足够强",
    原话的"更夸张"这个明确方向性在整理稿中被稀释,需要在下游还原。
  tags: [principle, script-adaptation, opening-hook, exaggeration-direction]
  evidence_tier: 原话
```

```yaml
- id: p14
  title: "★剧本改编·人物关系会改,但不全改"
  type: principle
  source_chapter: "21:00–21:24"
  source_quote: |
    "人物关系我们会去改啊,但我们人物关系不会全改。"(讲话人2)
  summary: |
    对人物关系的改动态度是一句独立的原则性表态,先于"怎么判断哪些能改"
    (见 p15)单独陈述:人物关系不是禁止改动的禁区,也不是可以随意全改的
    自由区,而是"部分可改、部分不可改"的混合区。
  tags: [principle, script-adaptation, character-relationship]
  evidence_tier: 原话
```

```yaml
- id: p15
  title: "★剧本改编·人物关系可变/不可变判定法:多爆款并排对照"
  type: rule
  source_chapter: "21:24–21:43"
  source_quote: |
    "爆款的人物关系,你多看几个,你发现那种男女关系或者父女关系……这种是不能
    乱改的。比如说两个都爆了,你看它们相似的关系保留的是什么?然后把那种不
    一样的你可以去改,一样的东西就不要去改。"(讲话人2)
  summary: |
    可操作的判定流程(原话给出,非整理层推断):
    ① 找≥2条同类型爆款并排对照;
    ② 两条都保留的人物关系(如男女关系、父女关系)→ 列为不可动;
    ③ 两条之间已经不一样、但各自都成功的元素 → 判定为安全可改区;
    ④ 口诀:"一样的东西就不要去改,不一样的可以改。"
    注意:原话到"关系类型"层面为止,没有说"保的是功能不是名称"这一步
    （见 p30 的整理层扩展区分）。
  tags: [principle, script-adaptation, character-relationship, comparison-method]
  evidence_tier: 原话
```

```yaml
- id: p16
  title: "★剧本改编·剧情底层逻辑链条一定不能变"
  type: principle
  source_chapter: "21:52–22:19"
  source_quote: |
    "剧情的底层逻辑这个东西是一定不要变的啊……那测试他一开始设局,那你这个
    设局就可以有很多地方可以改的,但是一定都是以开始然后假对象露出真相,
    这个东西怎么露出中间是不是?"(讲话人2)
  summary: |
    以"测试人心"类剧情为例,对方给出的底层逻辑骨架只有三个节点:
    设局开始 → 中间催化事件 → 假象拆穿/真相露出。
    这三个节点本身构成"底层逻辑",是改编时绝对不可动的部分。这是通话中
    唯一被讲话人2 明确称为"一定不要变"的元素,权重最高。
  tags: [principle, script-adaptation, core-causal-chain, hard-constraint]
  evidence_tier: 原话
```

```yaml
- id: p17
  title: "★剧本改编·链条每个节点的具体实现方式是自由发挥区"
  type: rule
  source_chapter: "22:19–22:39"
  source_quote: |
    "比如说生病了,被车撞了或者破产了这个东西你是可以有很多发挥的地方的。
    我们一般是这种地方,其他的就是改的地方都是这些,还有一点……就这两点
    比较重要,我觉得其他就没啥,因为开头还有一个人物关系这些。"(讲话人2)
  summary: |
    与 p16 互为一体两面:三节点骨架不能变,但每个节点"怎么实现"(为什么设局
    /用什么方式设局/中间用什么事件催化/真相怎么被揭露)是完全开放的自由
    发挥空间。讲话人2 自己在段落结尾做了收尾式总结,明确点名"就这两点比较
    重要"(=底层逻辑链 p16/p17 + 开头 p13 + 人物关系 p14/p15),没有第三个要点,
    这确认了本段(21:00–22:39)已被讲话人2 自己封顶为"改本子的全部要点"。
  tags: [principle, script-adaptation, free-variation-zone, core-vs-surface]
  evidence_tier: 原话
```

```yaml
- id: p18
  title: AI洗稿产出后必须人工过一遍,不能直接生成即用
  type: rule
  source_chapter: "24:23–24:47"
  source_quote: |
    "量是肯定是有的,但是……不能太同质化,你是一下子交给AI去洗出来,这种还是
    得过一下。"(讲话人2)
  summary: |
    对方明确反对"AI 洗完直接投产"的做法,强调批量生产之后仍需要一道人工
    过稿关卡,这是他给出的生产纪律而非选择项。
  tags: [rule, production-discipline, human-review]
  evidence_tier: 原话
```

```yaml
- id: p19
  title: 日产量应控制在6-10条,优先提升质量而非追求数量
  type: rule
  source_chapter: "24:23–25:00"
  source_quote: |
    "你像那个团队他一个人去写,一天顶多写八条,他只写脚本儿的,他还不干其他
    的……你可以先把量控制到一天,其实一天8到10条就是很ok了,或者6到8条都
    很ok了,把质量再去提升一些。"(讲话人2)
  summary: |
    对方以"常州团队"专职写手日产八条为参照案例(转述,非对方亲身数据),
    给出的建议是把日产量控制在6-10条区间,把节省出的时间用于质量提升,
    而不是一味追求单日条数最大化。
  tags: [rule, production-quota, quality-over-quantity]
  evidence_tier: 原话-转述（参照案例来自第三方常州团队，非对方亲身实操数据）
```

```yaml
- id: p20
  title: 第三方提示词的核心价值仅在于"先提炼故事底层逻辑链条再洗"
  type: principle
  source_chapter: "07:09–07:23"
  source_quote: |
    "他会把故事的底层逻辑链条这一块啊……他会把测试的那些剧本的核心的故事
    链条提炼出来。"(讲话人2)
  summary: |
    对方转述常州团队的提示词,并明确指出其唯一被看重的价值点就是"提炼底层
    逻辑链条再洗"这一步,与 p16 的原则完全对应——说明"保底层逻辑链"不是
    孤立的一句话,而是被两个不同信息源(对方团队自己的做法 + 转述的第三方
    提示词)反复印证的共同做法。
  tags: [principle, script-adaptation, cross-validation]
  evidence_tier: 原话-转述
```

```yaml
- id: p21
  title: 产能已经不是第一瓶颈,有效产出率与商业转化才是
  type: principle
  source_chapter: "§二 / §十三-1"
  source_quote: |
    "继续单纯扩大生成量,只会更快地放大当前选品、脚本和转化上的问题。自动化
    是放大器,不会自动修正运营判断。"(整理稿 §二)
  summary: |
    整理稿对通话内容的高层次总结:自动化解决的是产量和成本,不解决"什么值得
    打、怎么改、怎么承接"这些判断性问题。原话中虽无逐字对应句,但与讲话人1
    反复描述"效率有了、质量提不上来"(01:36)、"素材出不来钱"(13:17)的实际
    困境高度吻合,是对整场通话议题的合理归纳。
  tags: [principle, bottleneck-reframing, meta]
  evidence_tier: 整理层
```

```yaml
- id: p22
  title: "不跑量先定义具体症状,不同症状对应不同假设"
  type: checklist
  source_chapter: "§十一-1 / §十二-A"
  source_quote: |
    "以后遇到不跑量,不能只说素材不行、运气不好、平台不给流量……应该先区分
    具体症状:完全没有初始流量;有初始流量但很快停止;播放可以增长但商品
    没人点击;有点击没有成交;有成交但退款高;视频偶尔跑出但无法稳定复制。"
    (整理稿 §十一/§十二-A)
  summary: |
    把"不跑量"这一笼统抱怨拆成六种互斥症状的自查清单。原话中确有分开诊断
    "播放"与"转化"两类问题的做法(见 p01),但六项症状的完整清单是整理稿的
    结构化扩展,原话没有逐条列出。
  tags: [checklist, symptom-definition, diagnosis]
  evidence_tier: 整理层
```

```yaml
- id: p23
  title: "别人能爆我不能爆"应视为反证而非自我否定的理由
  type: maxim
  source_chapter: "§十一-2"
  source_quote: |
    "'别人能爆,我不能爆'是一条反证,不是理由。它说明:我以为自己已经满足了
    成功条件,但实际上仍然存在没有识别出的关键差异。"(整理稿)
  summary: |
    一条元层面的认知箴言,把焦虑性归因转换为"存在未识别变量"的排查线索。
    原话中没有逐字对应句,是整理者对通话中反复出现的"我这边也有这种流速
    /那我这边也可以干"等对比情绪的提炼总结。
  tags: [maxim, reframing, meta-cognition]
  evidence_tier: 整理层
```

```yaml
- id: p24
  title: 处理未知的通用程序:现象→分层假设→排序→单变量验证→记录→更新
  type: checklist
  source_chapter: "§十一-3"
  source_quote: |
    "1.准确描述现象;2.从商品、内容、转化、账号和环境多个层面提出假设;
    3.给假设排序;4.一次只改变少量关键变量;5.记录结果;6.排除错误解释;
    7.把验证后的结论加入自己的运营系统。"(整理稿)
  summary: |
    七步程序,整理稿称之为"元方法"。注意:这更接近"怎么想"的思维结构而非
    单纯的"做不做"判断规则,与 framework-extractor 的职责边界(思维模型/
    推理结构)存在重叠,按"不做筛选"原则仍作为候选提出,建议阶段1.5判定
    时与 framework 候选做去重比对。BOOK_OVERVIEW 的"最强反对意见"已指出:
    样本量小、变量互相纠缠时,"一次只改一个变量"的理想实验很难做到,这条
    程序可能退化为"可解释感"而非真实因果,使用时需保留怀疑。
  tags: [checklist, framework-boundary-overlap, hypothesis-testing]
  evidence_tier: 整理层
```

```yaml
- id: p25
  title: 不跑量诊断清单(商品层/内容层/转化层/账号环境层/验证层五层自查表)
  type: checklist
  source_chapter: "§十二 B-F"
  source_quote: |
    "B.商品层:商品处于起量、高峰还是下坡阶段?……C.内容层:题材和框架是否
    已经被大量账号反复使用?……D.转化层:是否加入真实产品画中画?……
    E.账号与环境层:账号是否存在违规记录?……F.验证层:这次只改变了什么
    变量?"(整理稿 §十二)
  summary: |
    整理稿把通话中散落的多个判断点(品的周期 p02、同质化 p11、画中画 p04、
    账号违规 p10 等)统一组织成一份五层自查清单,是全篇最完整的结构化清单,
    但清单形式本身是整理层的组织加工,原话中没有以"清单"形式出现。
  tags: [checklist, comprehensive-diagnosis, aggregated]
  evidence_tier: 整理层（内容多数可溯源至原话单点，清单结构本身是整理层组织）
```

```yaml
- id: p26
  title: AI稿人工审稿应覆盖七项:时序/人物一致性/动机/铺垫/冲突强度/植入自然度/相似度
  type: checklist
  source_chapter: "§八"
  source_quote: |
    "AI生成后的检查至少应覆盖:时间顺序是否正确;人物关系是否前后一致;
    人物行为是否有动机;铺垫是否足以支撑反转;核心冲突是否被改弱;商品
    植入是否自然;与原对标是否仍然过于相似。"(整理稿 §八)
  summary: |
    原话中对方只明确提到"倒序"问题和"还是得过一下"(见 p05、p18)这两点,
    没有给出七项清单。七项细化是整理稿的扩展加工,需按整理层标注,不能
    当作对方逐条确认过的审稿标准。
  tags: [checklist, human-review, quality-gate]
  evidence_tier: 整理层（原话仅覆盖"倒序""过一下"两点，七项细化为整理层扩展）
```

```yaml
- id: p27
  title: 二创与洗稿的产能比例应由数据决定,不能凭个人习惯分配
  type: rule
  source_chapter: "§七"
  source_quote: |
    "二创和洗稿不能因为谁更擅长、谁觉得省事就自然分工。应该分别记录:单条
    制作时间;人工审核时间;发布后的播放和流速;商品点击和成交;千次播放
    销售额;跑出素材的比例。再根据数据决定二创与洗稿的产能比例。"(整理稿)
  summary: |
    原话只确认了"二创+洗稿两条路线并行"这一事实(见 p07),没有提出"记录
    六项指标再决定比例"这一具体操作方法,这是整理层在原则之上叠加的可
    执行化扩展。
  tags: [rule, capacity-allocation, data-driven]
  evidence_tier: 整理层（"两条路线并行"是原话，"按六项指标定比例"是整理层扩展）
```

```yaml
- id: p28
  title: 口头经验数据一律先记为待验证假设,不能直接采信为规律
  type: rule
  source_chapter: "通篇纪律 / 说明段"
  source_quote: |
    "录音中的口头数据均不能直接视为平台规律,需要用自己的数据复验。"(整理稿
    开篇说明)
  summary: |
    这是整理者在文档开篇声明的、贯穿全文的方法论纪律,适用于本清单中所有
    标注"待验证"的数字(接近十倍转化差、一万播放一千元、账号单日限额、
    播放卡位8000/3万/8万等)。属于整理层给出的元规则,但与本次任务说明中
    "口头数据一律标为待验证,不得写成确定规则"的要求完全一致,建议下游
    直接采纳为强约束。
  tags: [rule, epistemic-hygiene, unverified-data, meta]
  evidence_tier: 整理层（但与本任务纪律要求一致，建议保留为强约束）
```

```yaml
- id: p29
  title: 选品要看"最近新素材是否仍能稳定起量出单"等动态指标,而非"历史上有没有人爆过"
  type: checklist
  source_chapter: "§三"
  source_quote: |
    "不能只看'有没有人爆过',还要观察:最近仍然持续有人出单,还是只有旧素材
    留下的数据;新发布素材是否还能稳定起量;同类账号近期是否继续投入;商品
    是否开始频繁换链接、降价、加大佣金或促销;新素材的播放和成交趋势是在
    上升还是下降。"(整理稿 §三)
  summary: |
    原话只给出"品的周期三阶段"这个框架和"红烧酱汁在下坡期,建议换品"这个
    具体判断(见 p02、p03),没有列出这五项动态观察指标,是整理层针对
    "如何识别当前阶段"这一操作性问题的扩展细化。
  tags: [checklist, product-selection, dynamic-indicators]
  evidence_tier: 整理层
```

```yaml
- id: p30
  title: "（整理层推断，非原话）人物关系保的是功能而非名称"
  type: principle
  source_chapter: "§六-3（原话对照版已标注为推断层）"
  source_quote: |
    "真正需要保留的未必是'父女'这个名称,而是父女关系在原故事中承担的信任
    和情感功能。如果换成夫妻、师徒或朋友后仍能保持相同功能,人物关系可能
    可以变化。"(整理稿 §六)
  summary: |
    ⚠️ 明确标注为整理层推断,原话止步于"关系类型对照,一样的不要改"
    (见 p15),对方从未说过"功能等价可换"这一步。原话对照版(用户已于
    2026-08-03 确认)明确指出这是"合理推断,未经他确认,使用时按待验证
    假设对待"。下游 skill 化时,此条不应与 p15 混为同一确定性层级。
  tags: [principle, script-adaptation, character-relationship, low-confidence-inference]
  evidence_tier: 整理层（原话对照版已明确标注为推断，非对方确认）
```

```yaml
- id: p31
  title: "（整理层推断，非原话）底层逻辑链细化为五步"
  type: principle
  source_chapter: "§六-2（原话对照版已标注为推断层）"
  source_quote: |
    "主角出于某种目的设局;目标人物在错误信息下做出选择;中间发生事件,让
    人物立场和利益暴露;假象被拆穿;真相出现,完成反转和情绪释放。"(整理稿
    §六,五步链)
  summary: |
    ⚠️ 原话给出的骨架只有三个节点(设局开始→中间催化事件→假象拆穿/真相
    露出,见 p16),整理稿把它细化成五步。细化本身可能有实用价值,但这是
    "我们的模型",不是对方验证过的说法,不应标注为对方原则,只能作为在
    p16 三节点基础上的可选细化版本使用。
  tags: [principle, script-adaptation, core-causal-chain, low-confidence-inference]
  evidence_tier: 整理层（原话对照版已明确标注为整理时细化，非对方原话）
```

```yaml
- id: p32
  title: 画中画的六项功能假说
  type: checklist
  source_chapter: "§四"
  source_quote: |
    "1.让用户明确知道视频正在卖什么;2.把剧情注意力转移到商品上;3.展示真实
    产品、使用场景和结果,降低不信任;4.提醒用户点击购物链接;5.让剧情与
    商品之间产生更自然的承接;6.在用户没有看完整条视频时,提前完成商品
    曝光。"(整理稿 §四)
  summary: |
    原话只说了"加画中画能提升转化,接近十倍"(见 p04),没有展开画中画具体
    起什么作用。六项功能是整理层基于"画中画"这个概念常识做的功能拆解,
    不是对方逐条陈述的内容。
  tags: [checklist, pip, conversion-mechanism]
  evidence_tier: 整理层
```

```yaml
- id: p33
  title: 应固定记录点击率/成交率/千次播放销售额/退款率,自行验证转化提升幅度
  type: rule
  source_chapter: "§四"
  source_quote: |
    "以后应固定记录商品点击率、点击后的成交率、千次播放销售额和退款率。
    对方所说的'接近十倍'只能作为测试依据,不能直接作为我们的预期结果。"
    (整理稿 §四)
  summary: |
    整理层给出的操作纪律,直接呼应 p28 的"待验证"要求——不是照搬对方数字,
    而是自建四项指标的记录体系去验证。这是"待验证假设"原则在画中画场景
    下的具体落地方法。
  tags: [rule, metrics-tracking, self-verification]
  evidence_tier: 整理层
```

```yaml
- id: p34
  title: "第三方账号/自动化方案需另行核实五项(合规/风险/来源/数据/质保)"
  type: checklist
  source_chapter: "§十"
  source_quote: |
    "涉及第三方账号、共享账号、逆向接口或质保承诺时,需要另行核实:是否
    违反平台条款;是否有封号或资产损失风险;账号来源是否合法、稳定;生成
    内容和数据是否会泄露;所谓'质保'是否真实可执行。"(整理稿 §十)
  summary: |
    原话中讲话人1 向讲话人2 推销一套第三方TK账号/自动化方案(套餐价格、
    积分规则、"质保一个月"等,09:18–13:57、23:44),纯属口头交流且讲话人1
    是方案的经手/推荐方,存在利益相关。BOOK_OVERVIEW 已将 §十 标注为
    "不适合skill化,只留作风险核对案例";此清单是整理层基于该风险场景
    给出的核实项,原话中双方并未讨论过这五项核实标准本身。按"不做筛选"
    原则仍作为候选提出,但建议阶段1.5判定时降低优先级或归入风险案例而
    非可执行规则。
  tags: [checklist, third-party-risk, compliance, low-priority-per-overview]
  evidence_tier: 整理层（涉及利益相关信息源，§十已被 BOOK_OVERVIEW 标记为不适合 skill 化）
```

```yaml
- id: p35
  title: "（原话-非对方）批量自动化提交比人力逐条提交更划算"
  type: principle
  source_chapter: "11:28–11:52"
  source_quote: |
    "人力去搞,你不划算的吧,人力一张一张提交……我提交40个分镜可能只要5
    分钟。"(讲话人1)
  summary: |
    发言人是讲话人1(我方),陈述的是自己工具链的自动化优势,不是张立浩的
    经验主张;张立浩关心的是接入方式而非划算与否本身。与本项目"批量生图
    一次性全量出、不做单张试跑"的既有硬性规则(见 CLAUDE.md 规则12)方向
    一致,可作为外部佐证,但不宜标注为"对方原则"。
  tags: [principle, automation-economics, attribution-caution]
  evidence_tier: 原话-非对方
```

```yaml
- id: p36
  title: 最终要建立能回答"为什么能跑/为什么能卖/什么可复制"的运营系统
  type: maxim
  source_chapter: "§十三 结尾"
  source_quote: |
    "最终要建立的,不只是一个自动生产视频的系统,而是一套能够持续回答下面
    三个问题的运营系统:为什么这条能跑?为什么这条能卖?哪些条件可以稳定
    复制?"(整理稿 §十三结尾)
  summary: |
    整篇蒸馏稿的收尾箴言,统摄前面所有分层诊断(选品/内容/转化/账号/生产
    配比),把目标从"能不能爆一条"重新定义为"能不能持续解释和复制爆的
    原因"。原话中没有逐字对应句,是整理者对通话主旨的总结性提炼。
  tags: [maxim, closing-statement, systems-thinking]
  evidence_tier: 整理层
```
