/* 部署态验收：拿**真实样本**喂页面自己的判定函数（2026-10-14，Chief 要的）。
 *
 * 用法：
 *   NODE_PATH=<jsdom 所在 node_modules> node docs/patrol/console-ui/verify-deploy-samples.js \
 *       web/console/index.html .scratch/deploy-acceptance
 *
 * 为什么不在真机上点：浏览器只连 console 单一 origin，而导出样本就是**页面自己那条视图**
 * （`GET :8002`）的响应，形状与页面所见逐字相同（地址类字段已脱敏）。
 * 这份脚本做三件事，全部对着"真形状"而不是"设想的形状"：
 *   1. **契约**：页面消费的字段必须真在响应里（缺字段 = 页面读到 undefined 的那种静默坏）；
 *   2. **形状**：布尔 vs 1、对象 vs 字符串、NULL vs 干净——逐条打印实测类型；
 *   3. **判定**：把真行喂给页面自己的 `integrityOfRound()` / `frameDupInfo()`，看它落到哪一档。
 *
 * 退出码：有 FAIL 就非零（可直接当门禁）。
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const htmlPath = process.argv[2] || 'web/console/index.html';
const sampleDir = process.argv[3] || '.scratch/deploy-acceptance';
const html = fs.readFileSync(htmlPath, 'utf8');
const out = [];
const check = (name, ok, detail = '') => {
  out.push(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  → ' + detail : ''}`);
  return ok;
};
const read = f => JSON.parse(fs.readFileSync(path.join(sampleDir, f), 'utf8'));
const rowsOf = d => d.rows || d.rounds || d.round || [];
const at = arr => (Array.isArray(arr) ? arr[0] : arr);

// 页面消费的字段（与 `verify-page.js` 的 PAGE_CONSUMES 同源；改页面时两处一起改）
const CONSUMED = {
  rounds: ['round_id', 'ts', 'ok', 'n_results', 'n_failures', 'room_id', 'entry_date',
           'batch_no', 'suspect_duplicate', 'dup_groups', 'content_check_error',
           'n_duplicate', 'n_frames_recorded'],
  images: ['ts', 'round_id', 'station_id', 'box_id', 'angle_profile', 'object_name',
           'ok', 'source', 'frame_dup', 'dup_group', 'content_sha256', 'same_as_previous',
           'duplicate_streak', 'elapsed_s'],
};

(async () => {
  const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true,
                                url: 'http://console.local/' });
  await new Promise(r => setTimeout(r, 800));       // 让页面脚本装好（与 verify-page.js 同做法）
  const w = dom.window;
  if (typeof w.integrityOfRound !== 'function' || typeof w.frameDupInfo !== 'function') {
    console.error('页面里找不到 integrityOfRound/frameDupInfo —— 页面版本不对？');
    process.exit(2);
  }

  // ---------- 1. 轮级 ----------
  const roundFiles = {
    '正常轮 73dd304c（10-13 21:27）': 'round-normal-73dd304c.json',
    '事故轮 12038e7a（10-09 18:20，legacy）': 'round-accident-12038e7a.json',
    '最新轮 485d126a（10-14 09:27）': 'round-latest-485d126a.json',
  };
  const verdicts = {};
  for (const [label, f] of Object.entries(roundFiles)) {
    const row = at(rowsOf(read(f)));
    if (!check(`样本存在且是轮行：${label}`, !!row && !!row.round_id, row && row.round_id)) continue;
    const missing = CONSUMED.rounds.filter(k => !(k in row));
    check(`契约：${label} 的响应含页面消费的全部字段`, missing.length === 0, missing.join(',') || 'ok');
    verdicts[label] = {
      ok: row.ok, n_frames_recorded: row.n_frames_recorded, n_duplicate: row.n_duplicate,
      suspect_duplicate: row.suspect_duplicate, dup_groups: row.dup_groups,
      content_check_error: row.content_check_error,
      verdict: w.integrityOfRound(row),
    };
  }
  for (const [label, v] of Object.entries(verdicts)) {
    out.push(`INFO  ${label} 真值：ok=${JSON.stringify(v.ok)} n_frames_recorded=${JSON.stringify(v.n_frames_recorded)}` +
             ` n_duplicate=${JSON.stringify(v.n_duplicate)} suspect_duplicate=${JSON.stringify(v.suspect_duplicate)}` +
             ` dup_groups=${JSON.stringify(v.dup_groups)} → 页面判定 ${v.verdict.level} ${JSON.stringify(v.verdict.label)}`);
  }
  const norm = verdicts['正常轮 73dd304c（10-13 21:27）'];
  check('真形状：正常轮零痕迹（`n_duplicate=0` 是唯一的正面证据，其余三列为 NULL）',
    !!norm && norm.verdict.level === 'ok' && norm.verdict.label === '',
    norm ? `${norm.verdict.level}/${JSON.stringify(norm.verdict.label)}` : '(缺)');
  const acc = verdicts['事故轮 12038e7a（10-09 18:20，legacy）'];
  check('真形状：10-09 事故轮落「未核查（那时没有内容核查）」，**不许因为 `ok=1` 就判干净**',
    !!acc && acc.verdict.level === 'unchecked' && acc.verdict.label.includes('那时没有内容核查'),
    acc ? `${acc.verdict.level}/${JSON.stringify(acc.verdict.label)}` : '(缺)');
  check('负向钉子：事故轮的 `ok=1` 不得被当成"干净"（同一份 `ok` 在两轮里取值相同）',
    !!acc && !!norm && acc.ok === norm.ok && acc.verdict.level !== norm.verdict.level,
    acc && norm ? `ok 都是 ${JSON.stringify(acc.ok)}，判定却分别是 ${acc.verdict.level}/${norm.verdict.level}` : '(缺)');

  // ---------- 2. 帧级：真机的 `duplicate_streak = 1` 是正常 ----------
  for (const [label, f] of Object.entries({
    '正常轮 73dd304c': 'images-normal-73dd304c.json',
    '事故轮 12038e7a': 'images-accident-12038e7a.json',
    '最新轮 485d126a': 'images-latest-485d126a.json',
  })) {
    const rows = rowsOf(read(f));
    if (!check(`样本存在且是帧行：${label}`, rows.length > 0, `${rows.length} 行`)) continue;
    const miss = CONSUMED.images.filter(k => !(k in rows[0]));
    check(`契约：${label} 的帧行含页面消费的全部字段`, miss.length === 0, miss.join(',') || 'ok');
    const streaks = [...new Set(rows.map(r => r.duplicate_streak))];
    const flagged = rows.filter(r => w.frameDupInfo(r).dup);
    out.push(`INFO  ${label}：duplicate_streak 取值 ${JSON.stringify(streaks)}，` +
             `frame_dup 取值 ${JSON.stringify([...new Set(rows.map(r => r.frame_dup))])}，` +
             `页面判为重复帧的 ${flagged.length}/${rows.length} 行`);
    check(`${label}：**不许把正常帧标成重复帧**（真机 \`duplicate_streak=1\` 是"它自己"）`,
      flagged.length === 0, flagged.length ? flagged.map(r => r.station_id).slice(0, 5).join(',') : 'ok');
  }

  // ---------- 3. `dup_groups` 非空：真数据里不存在（照实说，不造数据） ----------
  const probe = read('dup-groups-probe.json');
  check('真数据里 `dup_groups` 非空的行 = 0（探针是活的：`content_sha256` 有 240 行）',
    probe.n_hit === 0 && probe.n_rounds_scanned > 0,
    `n_hit=${probe.n_hit} / 扫了 ${probe.n_rounds_scanned} 轮`);
  out.push('INFO  `dup_groups` 是"对象数组"这条形状在真数据上**仍然无法验证**（一行都没有）；' +
           '由离线单测（巡检手 `aa9f51c`）与页面 fixture 覆盖，不为它造生产数据。');

  // ---------- 4. `/api/ranges` 信封（票 05 §5，2026-10-14 破坏性变更） ----------
  // 样本还没到就**明确跳过**（不是 FAIL）——它要等发版官那次 analysis 重建（`/api/ranges` 404→200）。
  const rangesFile = 'ranges-raw.json';
  if (!fs.existsSync(path.join(sampleDir, rangesFile))) {
    out.push('SKIP  `' + rangesFile + '` 还没导出 ⇒ **`/api/ranges` 的部署态验收未做**' +
             '（要等发版官那次 analysis 重建；`8002/api/ranges` 从 404 变 200 是验收硬指标）');
  } else {
    const doc = read(rangesFile);
    const calls = doc.calls || [{ request: doc.provenance || '(单条)', body: doc.body }];
    for (const c of calls) {
      const b = c.body;
      const label = c.request + '（http ' + (c.http === undefined ? '?' : c.http) + '）';
      if (Array.isArray(b)) {
        // 旧版裸列表：页面必须能读、且**不冒充"样本不足"**
        const env = w.rangesEnvelope(b);
        const html = w.rangesHtml(b);
        check(`旧版形状仍能读：${label}`, env.legacy === true && env.count === b.length,
              `legacy=${env.legacy} count=${env.count}`);
        check(`旧版形状：如实说"不知道算没算过"，**不许**冒充"样本不足"`,
              html.includes('旧版接口') && !html.includes('样本不足'), html.slice(0, 120));
        continue;
      }
      const keys = Object.keys(b || {});
      const missing = ['ok', 'rows', 'count', 'state', 'state_text', 'detail']
        .filter(k => !(k in (b || {})));
      check(`信封形状：${label} 六个必有键齐全`, missing.length === 0, missing.join(',') || keys.join(','));
      check(`信封形状：${label} 的 rows 是 list（不是字符串/对象）`,
            Array.isArray(b && b.rows), typeof (b && b.rows));
      if (b && b.state) {
        const html = w.rangesHtml(b);
        check(`空态：原样显示后端的 state/state_text（六态具名）`,
              html.includes(String(b.state)) && (!b.state_text || html.includes(String(b.state_text))),
              html.slice(0, 140));
        check(`空态：**不合并成"暂无数据"**（负向）`,
              !/暂无数据|没有数据/.test(html.slice(html.indexOf('区间表'))), html.slice(0, 120));
      }
      const det = (b && b.detail) || {};
      if (det.excluded_by_reason && Object.keys(det.excluded_by_reason).length) {
        const html = w.rangesHtml(b);
        const first = Object.keys(det.excluded_by_reason)[0];
        check(`排除可见：按原因计数（${first} ×${det.excluded_by_reason[first]}）出现在页面上`,
              html.includes(first) && html.includes(String(det.excluded_by_reason[first])),
              html.slice(html.indexOf('已排除'), html.indexOf('已排除') + 140));
        const smp = (det.excluded_samples || [])[0];
        if (smp) {
          check('排除可见：前 20 条**原文**带窗口与依据（可回答"排掉了哪一段、凭什么"）',
                html.includes(String(smp.ts)) && (!smp.window || html.includes(String(smp.window)))
                && (!smp.detail || html.includes(String(smp.detail))),
                html.slice(html.indexOf('已排除'), html.indexOf('已排除') + 200));
        }
        check('排除可见：只列前 20 条时标出"只列前 20 条"（不让人以为这就是全部）',
              !det.excluded_samples_truncated || html.includes('只列前 20 条'),
              String(det.excluded_samples_truncated));
      } else {
        out.push(`INFO  ${label} 的 detail 里没有 excluded_by_reason（本轮没有被排除的行）`);
      }
    }
  }

  console.log(out.join('\n'));
  const failed = out.filter(l => l.startsWith('FAIL')).length;
  console.log(`\n结果：${out.filter(l => l.startsWith('PASS')).length} PASS / ${failed} FAIL`);
  process.exit(failed ? 1 : 0);
})();
