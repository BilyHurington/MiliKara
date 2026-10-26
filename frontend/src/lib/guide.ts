// In-app guidance in one place: the "?" beside each card (what it is for, in a few points, and the part
// of the tutorial that says more), the tips shown the first time a page is opened, the steps overview of
// the detailed mode and the keyboard shortcuts.  Pages refer to these by key; the wording lives here so
// it stays consistent with itself and with docs/tutorial.md.  A "?" adds to what its card already says:
// it never repeats the card's own description.

import type { Step } from '@/store/app';

export const TUTORIAL = 'https://github.com/BilyHurington/MiliKara/blob/main/docs/tutorial.md';

/** Sections of docs/tutorial.md (GitHub's heading anchors). */
const DOC = {
  start: '#1-启动',
  simple: '#21-做一首',
  calibrateDialog: '#22-确认第一句从哪里开始唱',
  tasks: '#24-任务列表',
  settings: '#3-设置',
  pro: '#4-详细模式逐步检查和微调',
  input: '#音频与歌词',
  enhance: '#注音与分离',
  calibrate: '#首音校准',
  review: '#对齐--人工检查',
  singers: '#演唱者多人演唱时',
  karaoke: '#卡拉ok字幕',
  export: '#导出',
  faq: '#5-常见问题',
} as const;

export interface HelpEntry {
  title: string;
  points: string[];
  /** the section of the tutorial that says more */
  doc: string;
}

export const HELP = {
  // ------------------------------------------------------------------ both modes
  'ui.modes': {
    title: '两种模式怎么选',
    points: [
      '极简模式：放进视频和歌词，注音、分离、对齐、生成视频全部自动完成。大多数歌用它就够了。',
      '详细模式：一步一步来，每一步都能检查和修改：读音、对齐时间、多人演唱的颜色、字幕的每个细节。',
      '随时可以切换，不会丢失任何东西。极简模式做完的任务，点任务名就能在详细模式里打开继续改。',
    ],
    doc: DOC.start,
  },
  dock: {
    title: '播放器和波形',
    points: [
      '所有页面共用：点击波形跳到那里，拖动选出一段，按 L 循环播放这一段。',
      '可以切换播放原曲、人声或伴奏（分离之后），也可以放慢速度听清楚。',
      '滚轮或右侧按钮缩放波形；空格播放 / 暂停。',
    ],
    doc: DOC.pro,
  },
  storage: {
    title: '存储空间',
    points: [
      '可以删整个项目，也可以只删生成的视频，或只删分离出的分轨（之后需要时再分离）。',
      '缓存（解码的音频、波形、识别结果）和残留文件可以放心清理，用到时会重新生成。',
    ],
    doc: DOC.faq,
  },

  // ------------------------------------------------------------------ simple mode
  'simple.mode': {
    title: '选哪个模式',
    points: [
      '网易云 / QQ 音乐的歌词和 LRC 文件都带时间，选 LRC 增强：长前奏、间奏、重复的副歌更稳。只有文字时选普通。',
      '确认第一句的位置只要在波形上点一下；不想每次确认，可以在设置 →“歌词开头对齐”里改成自动检测（没把握时仍会问你）。',
    ],
    doc: DOC.simple,
  },
  'simple.media': {
    title: '视频或音频',
    points: [
      '放视频：字幕烧录在视频自己的画面上。',
      '只放音频：歌词来自音乐链接时，用模糊的歌曲封面做背景；否则是纯黑。',
      '音频 + 背景：再放一张图片，或一段循环播放的视频（不用它的声音）。背景会一直留着，连续做几首可以共用。',
    ],
    doc: DOC.simple,
  },
  'simple.lyrics': {
    title: '歌词',
    points: [
      '最方便：粘贴网易云 / QQ 音乐的歌曲链接（分享文字也行），歌词、翻译、歌名歌手一起取来。',
      '也可以直接粘贴 LRC 或纯文本歌词，再填上歌曲名。',
      '歌词里写着“A：”“【成员名】”这类演唱者时，可以在详细模式的“演唱者”里识别并分色。',
    ],
    doc: DOC.simple,
  },
  'simple.output': {
    title: '字幕与视频',
    points: [
      '配色：选模版和一两个主题色，其余颜色自动搭配；也可以用保存的预设或设置里的样式。',
      '声音可以多选：原唱、伴唱（去掉人声）、降低人声、无声，每选一种生成一个视频。伴唱和降低人声需要人声分离。',
      '这些选择会自动记住；添加任务时就固定到这个任务上，之后再改不影响已经排队的任务。',
      '想细调字体、位置、时间，到“设置 → 字幕样式”或详细模式。',
    ],
    doc: DOC.simple,
  },
  'simple.tasks': {
    title: '任务',
    points: [
      '黄色提示表示在等你：点里面的按钮确认开头位置或贴入 AI 的回复。等待中的任务不挡住后面的任务。',
      '鼠标停在一个步骤上，可以看到它在做什么、做到哪里。',
      '“有 N 处建议检查”会在详细模式的人工检查里打开那几行。',
      '失败的任务可以重试，做完的步骤不会重做；反馈问题时附上“复制诊断信息”。',
    ],
    doc: DOC.tasks,
  },
  'settings.ai': {
    title: 'AI 注音',
    points: [
      '手动（网页聊天）：不用安装也不用 API Key，复制提示词到任意 AI 网页，再把回复贴回来。',
      'Claude Code / Codex / API：全自动，不用等你操作；可以先“测试连接”。',
      '只发送歌词，不发送音频。',
    ],
    doc: DOC.settings,
  },
  'settings.separation': {
    title: '人声分离',
    points: [
      '模型：默认的 MelBand RoFormer 分得最干净；MDX-Net 快 2–3 倍，适合没有独立显卡的电脑。',
      '运行设备选“自动”会用显卡或 Apple 芯片；只用 CPU 时，可能要歌曲时长的 1–2 倍。',
      '分离时内存峰值约 3 GB；内存 16 GB 以下的电脑，分离时尽量少开别的程序。',
    ],
    doc: DOC.settings,
  },
  'settings.calibration': {
    title: '歌词开头对齐',
    points: [
      '标记时：空格从标记处播放，← → 微调 10 ms（按住 Shift 为 100 ms）；“试听中间一句”可以确认整首都对得上。',
      '确认之后，所有歌词按同样的偏移对齐，其余步骤全部自动完成。',
      '这一步就是详细模式里的“首音校准”，之后还可以在那里修改。',
    ],
    doc: DOC.calibrateDialog,
  },
  'settings.output': {
    title: '输出视频',
    points: [
      '关掉“自动生成视频”时，任务停在对齐完成，可以先在详细模式里检查，再到“卡拉OK字幕”生成。',
      '画质“高”文件更大、生成更慢。',
    ],
    doc: DOC.settings,
  },
  'settings.style': {
    title: '设置里的样式',
    points: [
      '和详细模式的字幕样式面板一样，每个分类旁的 ? 说明它管什么。',
      '可以从某个项目复制它调好的样式，也可以存成预设。',
    ],
    doc: DOC.karaoke,
  },

  // ------------------------------------------------------------------ detailed mode: home
  'home.new': {
    title: '新建项目',
    points: [
      '名字只是方便你找，之后可以改。',
      '创建后从第 2 步“音频与歌词”开始，左边的工作流程会标出每一步做到哪里。',
    ],
    doc: DOC.pro,
  },
  'home.import': {
    title: '导入项目',
    points: [
      '项目包（.kara.zip，在顶栏或导出页下载）可以带着音频，换一台电脑继续编辑。',
      'project.json 只有歌词、读音、时间和设置；音频需要在项目里重新上传。',
    ],
    doc: DOC.export,
  },

  // ------------------------------------------------------------------ 1 mode
  'mode.choice': {
    title: '普通和 LRC 增强',
    points: [
      'LRC 增强：用歌词里每行的时间帮模型定位，长前奏、间奏、重复的副歌不容易对错。建议先做“首音校准”。',
      '普通：只靠声音对齐，适合只有文字的歌词。',
    ],
    doc: DOC.pro,
  },

  // ------------------------------------------------------------------ 2 input
  'input.audio': {
    title: '音频',
    points: [
      '原曲可以是音频或视频；视频会无损提取声音，画面留着做卡拉OK视频。',
      '导入分轨时会检查它们和原曲是否同步。',
      '换了原曲，已有的对齐结果和分轨会标为过期。',
    ],
    doc: DOC.input,
  },
  'input.lyrics': {
    title: '歌词',
    points: [
      '可以粘贴文本、上传文件（LRC、TXT、prepared.json），或用网易云 / QQ 音乐链接获取（会带上翻译）。',
      '“作词：”“作曲：”这类行会认成作者信息，不参与对齐。',
      '歌词开头写着演唱者（“A：”“【成员】”）时，会提示到“演唱者”页识别：最好在对齐之前做。',
    ],
    doc: DOC.input,
  },
  'input.pairing': {
    title: '翻译 / 音译',
    points: [
      '配对先按时间一致，再按最近的时间，最后按顺序；每一行都可以手动改配给另一行。',
      '链接没带翻译时，可以在这里粘贴自己的翻译（LRC 或纯文本）。',
    ],
    doc: DOC.input,
  },
  'input.lines': {
    title: '当前歌词',
    points: [
      '“类型”：只有“歌词”会被对齐；翻译、音译、作者信息、空行不唱。',
      '关掉“对齐”的行不参与对齐，也不出现在字幕里（比如不唱的说明文字）。',
      '合并、拆分会保留来源和已指定的演唱者；删除要点两次确认。',
      '锚点：强制某一行从某个时间开始。一般不需要，只在某一段整体对不上时用。',
    ],
    doc: DOC.input,
  },

  // ------------------------------------------------------------------ 3 enhance
  'enhance.readings': {
    title: '读音',
    points: [
      '颜色表示读音从哪来：人工、AI、规则、导入；黄框是不确定、建议确认的。',
      '显示“？”的词还没有读音，不参与对齐：点它补上。数字（24）先按常见读法注音并标为待确认，唱法不同时点它修改。',
      '“确认”会锁定，之后规则和 AI 都不会再改它。',
      '读音写成片假名（宿敵 → ライバル），字幕注音就显示片假名；每个词也可以单独设定显示或不显示注音。',
    ],
    doc: DOC.enhance,
  },
  'enhance.ai': {
    title: 'AI 注音',
    points: [
      '规则对歌词的读音常常判断错，AI 准得多；建议对齐前做一次。',
      '一键 AI 注音要先配置 Claude Code、Codex 或 API（可以直接在这里设置，也可以在极简模式的设置里）。',
      '范围选“仅待确认”可以省 AI 额度。',
    ],
    doc: DOC.enhance,
  },
  'enhance.separation': {
    title: '人声分离',
    points: [
      '分离后默认用人声对齐，通常更准。',
      '显卡或 Apple 芯片上约 1–3 分钟；只用 CPU 慢很多，MDX-Net 模型快 2–3 倍。分离时内存峰值约 3 GB。',
      '已有分轨可以在“音频与歌词”里导入，就不用分离。',
    ],
    doc: DOC.enhance,
  },

  // ------------------------------------------------------------------ 4 calibrate
  'calibrate.calc': {
    title: '偏移是怎么算的',
    points: [
      '偏移 = 你标记的时间 − 歌词里写的时间（LRC 文件自带的 [offset] 也算在内），所有行一起移动；音频本身不会被剪切。',
      '“自动匹配”先试对齐一次，找出大多数行一致的偏移；试听确认后再应用。',
      '再标记一句会改掉偏移；只想检查，用下方的“中段 / 末段检查”。',
    ],
    doc: DOC.calibrate,
  },
  'calibrate.lines': {
    title: '选哪一句',
    points: [
      '默认第一句；开头唱得清楚的那一句最好标。',
      '点一行选中它，播放到它开始唱时按 M（或点“在播放头标记首个发音”）。',
    ],
    doc: DOC.calibrate,
  },
  'calibrate.check': {
    title: '中段 / 末段检查',
    points: [
      '选中中间或结尾的一句，播放到它开始唱时点“添加检查点”，看偏差多大。',
      '偏差都很小，说明整首一致；越往后偏差越大，说明视频和歌词的版本速度不同，可以给不准的行加锚点（“音频与歌词”里）。',
    ],
    doc: DOC.calibrate,
  },

  // ------------------------------------------------------------------ 5 align
  'align.ready': {
    title: '准备情况',
    points: [
      '对齐需要歌词和原曲音频；红色的项要先处理，点它跳到对应的步骤。',
      '黄色只是提醒：还有读音待确认、首音校准还没确认（LRC 增强建议先做）。',
    ],
    doc: DOC.review,
  },
  'align.settings': {
    title: '对齐设置',
    points: [
      '有人声分轨时默认用人声对齐，通常更准；某些行对不好时可以改用原曲比较一下。',
      '第一次对齐要加载模型，会慢一些；之后改读音、校准再对齐会快很多。',
    ],
    doc: DOC.review,
  },
  'align.advanced': {
    title: '高级解码参数',
    points: [
      '容差 σ：允许每行偏离歌词时间多远。歌词时间不太准时调大。',
      '先验强度 λ：多大程度上相信歌词里的时间。0 = 完全不信，只听声音。',
      '左 / 右边距：每行前后多找多长的音频。唱得比歌词时间早或拖得长时调大。',
      '硬锚点容差：人工锚点允许的误差。紧邻联合阈值：间隔很短的几句放在一起对齐。',
      '尾音策略：怎么处理一句最后拖长的音。异常句有限重试：对不好的行自动换几种方法再试一次。',
    ],
    doc: DOC.review,
  },
  'align.results': {
    title: '对齐结果',
    points: [
      '最新的结果自动成为“当前”（●）；字幕和导出用的都是当前结果，可以切回以前的结果。',
      '“提示”是可能需要看一眼的地方，到“人工检查”里处理。',
      '改了歌词、读音、校准、模式或换了原曲，结果会标为过期，重新对齐即可；人工改过的时间会保留。',
    ],
    doc: DOC.review,
  },

  // ------------------------------------------------------------------ 6 review
  'review.page': {
    title: '人工检查',
    points: [
      '改过的时间会锁定，重新对齐不会覆盖；顶栏的撤销可以退回。',
      '读音不对的话，先在“注音与分离”里改读音，再对这一行局部重跑。',
      '标着“置信度低”的行，常见于英文、括号里的和声：试听一下就知道对不对。',
    ],
    doc: DOC.review,
  },
  'review.list': {
    title: '歌词行列表',
    points: [
      '“有问题”只列有提示的行，“人工”只列改过的行。',
      '勾选几行后点“局部重跑”，只重新对齐这几行，得到的结果供对比，不会覆盖。',
    ],
    doc: DOC.review,
  },
  'review.detail': {
    title: '单元、提示、候选、局部重跑',
    points: [
      '单元：在表格里输入时间，或在波形上拖动单元两端。一整行都偏了用上方的“整行”：改行首整行平移，改行尾按比例伸缩。',
      '只偏了几个字：点一个单元，再 Shift 点另一个（或 ⌘ / Ctrl 逐个加选），在波形上拖动选区整体移动或伸缩。',
      '提示：这一行可能的问题和原因。候选、局部重跑：其他对齐方式的结果，试听后点“采用”才会生效。',
    ],
    doc: DOC.review,
  },

  // ------------------------------------------------------------------ 7 singers
  'singers.list': {
    title: '演唱者',
    points: [
      '点每人右边的箭头展开，可以逐项改颜色、上传头像。',
      '简称和头像用在每句的标注和开头的分色列表里；名字和简称里可以写彩色 emoji。',
      '同一组歌手的其他歌可以“存为预设”，以后直接载入；已指定的部分按名字对应。',
    ],
    doc: DOC.singers,
  },
  'singers.combos': {
    title: '组合快捷键',
    points: [
      '按完 1+2 后点上方的“存到 …”，或在这里添加；以后按一个键就是这几个人一起唱。',
      '组合可以有自己的效果（顺序不同也算同一个组合），比如三人用渐变、两人用上下分色。',
      '给组合起名字（如 全员），标注和分色列表里就只写这个名字。',
    ],
    doc: DOC.singers,
  },
  'singers.look': {
    title: '几个人一起唱',
    points: [
      '上下：每个字上下分开（1+2 就是上半 1 号、下半 2 号）；左右：一起唱的一整段从左到右分开。',
      '分色是几段清楚的颜色；渐变是平滑过渡。',
      '注音“自动”：上下分色时注音字太小，整个用第一位的颜色；左右时跟着所在的位置。',
    ],
    doc: DOC.singers,
  },
  'singers.labels': {
    title: '分色列表和标注',
    points: [
      '分色列表在歌曲信息之后、同一个角落列出每人的颜色，歌里至少两个人唱时才显示。',
      '每句标注：两人一起唱时分上下两小行，合起来和歌词一样高；三人以上平均分到两行。',
      '想在每句开头 / 结尾加符号、emoji 或图片，在“卡拉OK字幕”的字幕样式 →“歌词”里。',
    ],
    doc: DOC.singers,
  },
  'singers.markers': {
    title: '识别演唱者',
    points: [
      '把误认的（比如“Hey!”）取消勾选；“全员”“合”“ALL”这类会指定为所有人。',
      '可以顺便把名字从歌词里去掉：名字也会被当成歌词去对齐，所以最好在对齐之前做。',
    ],
    doc: DOC.singers,
  },
  'singers.preview': {
    title: '预览',
    points: [
      '选中歌词后，这里显示它唱之前、唱到一半或唱完时的画面，和最终视频一样。',
      '指定演唱者不需要对齐结果，但要对齐之后才能预览。',
    ],
    doc: DOC.singers,
  },

  // ------------------------------------------------------------------ 8 karaoke
  'karaoke.preview': {
    title: '预览',
    points: [
      '拖动时间，或在下拉框里选一行跳过去。',
      '“预览背景”只影响这里的预览，不影响生成的视频。',
      '选中一行后可以单独设定这一句前要不要倒计时。',
    ],
    doc: DOC.karaoke,
  },
  'karaoke.style': {
    title: '字幕样式',
    points: [
      '调好的样式可以“另存为”预设，在别的项目和极简模式里复用；内置的预设（默认、暖阳）不能改，可以另存一份再改。',
      '“设为极简默认”：极简模式之后的任务都用这套样式。',
      '每个分类旁的 ? 说明它管什么；鼠标停在分类上可以看到它现在的设置。',
    ],
    doc: DOC.karaoke,
  },
  'style.colors': {
    title: '配色',
    points: [
      '配色模版一次改好所有颜色：选朴素或荧光，再选一两个主题色。',
      '手动改过任何一个颜色后，就不再跟着模版走。',
      '“已唱描边”可以让唱过的部分换一种描边颜色。',
    ],
    doc: DOC.karaoke,
  },
  'style.text': {
    title: '歌词',
    points: [
      '高亮方式：平滑扫光是常见的卡拉OK效果；逐字变色是一个字一个字跳。',
      '每句开头 / 结尾可以加符号、彩色 emoji 或图片。',
      '荧光边缘是字周围的光晕，颜色在“配色”里改。',
    ],
    doc: DOC.karaoke,
  },
  'style.ruby': {
    title: '注音',
    points: [
      '在汉字上方标读音：平假名、片假名或罗马音；可以只标汉字。',
      '唱过的部分：“按注音时间”是每个读音自己变色；“与歌词对齐”是和下面的歌词一起扫过。',
      '个别词要不要显示注音，在“注音与分离”里点开这个词设定。',
    ],
    doc: DOC.karaoke,
  },
  'style.translation': {
    title: '翻译',
    points: [
      '位置：画面另一端（歌词在下时翻译在上）、歌词旁、或每行下方。',
      '多人演唱时，翻译的荧光可以跟着这一句的演唱者变色。',
    ],
    doc: DOC.karaoke,
  },
  'style.info': {
    title: '歌曲信息',
    points: [
      '开头在角落滑入歌名、歌手等，结尾再显示一次；可以选显示哪些，也可以自己写文字。',
      '被顶部的翻译挡住时，它会自动变窄让开。',
      '多人演唱的分色列表接在它之后显示（在“演唱者”页设置）。',
    ],
    doc: DOC.karaoke,
  },
  'style.layout': {
    title: '布局',
    points: [
      '同屏行数：同时显示下一句，方便跟唱；显示两三行时可以左右交替。',
      '长句放不下时：在空格、标点处分成两半轮流显示，或按 AI 建议的位置，或不换行。',
    ],
    doc: DOC.karaoke,
  },
  'style.timing': {
    title: '时间',
    points: [
      '歌词默认比实际演唱提前 150 ms 扫光，看起来正好在唱；整体感觉早或晚就调这里。',
      '提前出现、停留、淡入淡出控制每一行什么时候出现和消失。',
      '倒计时：第一句前和长间奏后，开唱前显示几个逐个消失的圆点。',
    ],
    doc: DOC.karaoke,
  },
  'style.effects': {
    title: '特效',
    points: [
      '唱到每个字时的效果，比如星光、花瓣、爱心，或者无。',
      '可以放在歌词后面或前面，可以调数量、大小和颜色。',
    ],
    doc: DOC.karaoke,
  },
  'karaoke.output': {
    title: '导出字幕 / 生成视频',
    points: [
      '下载 ASS 字幕，可以在别的软件里继续用；或直接生成带字幕的视频。',
      '画面：背景图片 / 视频、原视频或纯黑；歌词来自音乐链接时可以用模糊的歌曲封面。',
      '声音可以多选，每种生成一个视频。伴唱和降低人声需要先分离人声。',
    ],
    doc: DOC.karaoke,
  },

  // ------------------------------------------------------------------ 9 export
  'export.results': {
    title: '导出格式',
    points: [
      'LRC（逐行 / 逐字）给音乐播放器用；ASS 是卡拉OK字幕，给视频软件用。',
      'JSON / CSV 是完整的对齐数据，给其他程序处理用；prepared.json 是可以重新导入的歌词和读音。',
      '时间都从原音频开头算起。格式表达不了的信息（比如读音）会提示会丢失什么。',
    ],
    doc: DOC.export,
  },
  'export.mix': {
    title: '降低人声的混音',
    points: [
      '人声保留：原唱保留多少，0% 就是纯伴奏，20% 左右适合跟唱时听引导。',
      '伴奏和总增益调整音量；防削波避免声音过大破音。',
      '有视频时也可以直接生成带这段混音的视频（不带字幕；带字幕的视频在“卡拉OK字幕”里生成）。',
    ],
    doc: DOC.export,
  },
  'export.tracks': {
    title: '音轨',
    points: [
      '下载原曲、人声、伴奏的 WAV，时间起点和对齐结果一致，可以直接拿到别的软件里用。',
    ],
    doc: DOC.export,
  },
  'export.project': {
    title: '项目',
    points: [
      '含音频的项目包用来换一台电脑继续编辑；不含音频的包小得多，适合分享修改。',
      '字幕样式用到的图片（头像、句首句尾的图片）会一起打包。',
    ],
    doc: DOC.export,
  },
} satisfies Record<string, HelpEntry>;

export type HelpKey = keyof typeof HELP;

/** Plain introductions at the top of each step of the detailed mode. */
export const INTRO: Record<Step, string> = {
  mode: '先决定怎么对齐：歌词带时间（LRC）选 LRC 增强，只有文字选普通。之后随时可以改，输入和修改都会保留。',
  input: '放入原曲（音频或视频）和歌词。歌词可以粘贴、上传文件，或用网易云 / QQ 音乐链接获取；解析后先预览，确认了才会应用。',
  enhance: '可选，但推荐：读音对了，对齐才准。在这里检查和修改读音、用 AI 注音、分离人声。三项互不等待，可以按任意顺序做，也可以跳过。',
  calibrate: '歌词里的时间和这份音频常常整体差一段（片头、不同版本）。选一句歌词，在它真正开始唱的地方标记，所有行就会一起对上。极简模式里的“确认开头位置”就是这一步。',
  align: '点“开始对齐”，模型会听着音频找到每个字的时间。第一次要加载模型，需要稍等；完成后到“人工检查”看看有提示的地方。',
  review: '检查对齐结果：通常只需要看有提示的行，改过的时间会锁定。多人演唱的歌，接着去“演唱者”。',
  singers: '多人演唱时，给每位歌手不同的字幕颜色：先添加演唱者，再选中歌词按快捷键指定。单人演唱的歌可以跳过这一步。',
  karaoke: '预览任意时刻的画面，和最终视频完全一样；调整字幕样式，改动自动保存。完成后下载 ASS 字幕，或直接生成带字幕的视频。',
  export: '导出对齐结果、歌词、降低人声的混音、音轨和项目包。不同格式给不同的软件用，点每一类的 ? 看看哪个适合你。',
};

/** Tips shown the first time (dismissed: never again, until “重新显示使用提示”). */
export const TIPS = {
  'simple.welcome': {
    title: '第一次使用？',
    body: '几分钟后就能下载带字幕的视频，最耗时的是人声分离。LRC 歌词会先请你点一下第一句从哪里开始唱（设置里可以改成自动检测）。做完的任务点任务名，可以在详细模式里继续修改。每一步旁边的 ? 有简短说明。',
  },
  'review.first': {
    title: '怎么检查',
    body: '先切到“有问题”，只看有提示的行。点一个发音单元（或按 ↑ / ↓）后按 Enter 循环试听；时间不对就在波形上拖动单元的两端，或在表格里直接输入。',
  },
} as const;

export type TipKey = keyof typeof TIPS;

/** The steps of the detailed mode at a glance (the overview): when each is needed and what it does. */
export const STEP_GUIDE: Record<Step, { when: string; tone: 'accent' | 'info' | 'neutral'; what: string }> = {
  mode: { when: '新建时已选', tone: 'neutral', what: '普通或 LRC 增强' },
  input: { when: '必做', tone: 'accent', what: '放入音频和歌词' },
  enhance: { when: '推荐', tone: 'info', what: 'AI 注音让对齐更准；分离人声' },
  calibrate: { when: '仅 LRC 增强', tone: 'info', what: '标出第一句开始唱的位置' },
  align: { when: '必做', tone: 'accent', what: '运行模型，得到每个字的时间' },
  review: { when: '有提示时', tone: 'neutral', what: '看看有提示的行，修正时间' },
  singers: { when: '多人演唱时', tone: 'neutral', what: '给每人一种颜色' },
  karaoke: { when: '要视频时', tone: 'neutral', what: '调字幕样式，生成带字幕的视频' },
  export: { when: '按需', tone: 'neutral', what: '导出 LRC、ASS、混音或项目包' },
};

/** What each step of a simple-mode task does (its chip's tooltip). */
export const STAGE_WHAT: Record<string, string> = {
  import: '读取视频或音频，提取声音',
  lyrics: '从链接获取或解析歌词和翻译',
  calibrate: '找出视频和歌词时间之间的整体偏移',
  readings: '让 AI 检查每个字的读音',
  separate: '把人声和伴奏分开（最耗时）',
  align: '找出每个字唱的时间',
  export: '把字幕烧录进视频',
};

export type ShortcutGroup = 'global' | 'calibrate' | 'review' | 'singers';

/** [keys pressed together, what they do]; "Mod" is ⌘ on macOS, Ctrl elsewhere. */
export const SHORTCUTS: Record<ShortcutGroup, { title: string; keys: [string[], string][] }> = {
  global: {
    title: '所有页面',
    keys: [
      [['Space'], '播放 / 暂停'],
      [['L'], '循环播放波形上选出的一段'],
      [['Mod', 'Z'], '撤销（人工检查里改的时间、演唱者的指定）'],
      [['Mod', 'Shift', 'Z'], '重做'],
      [['?'], '打开这张快捷键表'],
    ],
  },
  calibrate: {
    title: '首音校准',
    keys: [[['M'], '在播放位置标记所选这一句的开头']],
  },
  review: {
    title: '人工检查',
    keys: [
      [['↑ / ↓'], '选上一个 / 下一个发音单元'],
      [['Enter'], '循环试听所选单元'],
    ],
  },
  singers: {
    title: '演唱者',
    keys: [
      [['1–9 / A–Z'], '把选中的部分指定给这个快捷键的演唱者或组合（L、P 除外）'],
      [['+'], '再按另一个人的键：几个人一起唱'],
      [['0 / Backspace'], '清除选中部分的演唱者'],
      [['P'], '试听选中的部分'],
      [['Esc'], '取消选择'],
      [['Mod', 'A'], '全选歌词'],
    ],
  },
};
