#!/usr/bin/env node
/**
 * 证明「等值替换、渲染值不变」——**是证明，不是声称**。
 *
 * 用法：
 *   node docs/patrol/console-ui/diff-resolved-values.js <改前版本文件> <改后版本文件>
 * 取"改前版本"的例子（不检出、不 stash）：
 *   git show '<commit>^:web/console/index.html' > /tmp/before.html
 *
 * 干什么：把两份 `index.html` 里**每一个 `border-radius` / `padding*` 用点**解析成具体值
 * （`var(--token)` 递归展开，`:root` 里的定义作为唯一来源），再按
 * 「选择器 + 该选择器内第 N 处该属性」逐条对齐，输出：
 *   - 解析值完全相同的用点数；
 *   - 有差异的用点（改前值 → 改后值 + 两边的原文），**这一行就是"改了渲染"的清单**；
 *   - 只在一边存在的用点（规则被删/改名）。
 *
 * 为什么需要它（2026-10-14，Chief 的验收写法）：token 化最容易出的不是 bug 而是
 * **口径漂**——"等值替换"顺手换错一个台阶、或悄悄改了媒体查询里那一档，
 * 谁也不会在 code review 里逐条心算 105 个用点。跑一遍就有清单，差一条都算改了口径。
 *
 * 约定：差异 **必须**是零，或有明确交代（写进票面：哪一处、为什么）。
 * 已知的交代例子（票 11 第三批）：`.card` 圆角 9px→10px（与 KPI/大图面板统一）、
 * 矮屏 `.card` 横向内边距 12px→14px（矮屏不再缩横向留白）。
 */
const fs = require('fs');

function styleOf(html){
  const a = html.indexOf('<style>'), b = html.indexOf('</style>');
  // 去掉 CSS 注释：否则"在规则前加一段注释"会让选择器文本变样，看着像规则被改名
  return html.slice(a + 7, b).replace(/\/\*[\s\S]*?\*\//g, ' ');
}
function tokensOf(style){
  // :root 里的自定义属性（本文件的尺度 token 都定义在这里）
  const root = style.match(/:root\{([\s\S]*?)\}/);
  const map = {};
  if(root) for(const m of root[1].matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) map[m[1]] = m[2].trim();
  return map;
}
function resolve(v, map){
  let out = v, n = 0;
  while(/var\(/.test(out) && n++ < 10){
    out = out.replace(/var\((--[a-z0-9-]+)(?:\s*,\s*([^)]+))?\)/g, (_, name, fallback) =>
      (map[name] !== undefined ? map[name] : (fallback || '??')));
  }
  return out.replace(/\s+/g, ' ').trim();
}
function points(style){
  const map = tokensOf(style);
  const list = [], seen = {};
  for(const m of style.matchAll(/([^{}]*)\{([^{}]*)\}/g)){
    const sel = m[1].trim().replace(/\s+/g, ' ');
    for(const d of m[2].matchAll(/(border-radius|padding(?:-[a-z]+)?)\s*:\s*([^;}]+)/g)){
      seen[sel] = (seen[sel] || 0) + 1;
      list.push({ key: sel + ' #' + seen[sel] + ' · ' + d[1],
                  raw: d[2].trim(), val: resolve(d[2].trim(), map) });
    }
  }
  return list;
}

const head = points(styleOf(fs.readFileSync(process.argv[2], 'utf8')));
const now  = points(styleOf(fs.readFileSync(process.argv[3], 'utf8')));
const hMap = new Map(head.map(p => [p.key, p])), nMap = new Map(now.map(p => [p.key, p]));
let same = 0; const diff = [], onlyH = [], onlyN = [];
for(const k of [...new Set([...hMap.keys(), ...nMap.keys()])]){
  const h = hMap.get(k), n = nMap.get(k);
  if(h && !n){ onlyH.push(h); continue; }
  if(n && !h){ onlyN.push(n); continue; }
  if(h.val === n.val) same++;
  else diff.push({ k, before: h.val, after: n.val, beforeRaw: h.raw, afterRaw: n.raw });
}
console.log('用点总数：改前 ' + head.length + ' ，改后 ' + now.length);
console.log('解析值完全相同的用点：' + same + ' 处');
console.log('解析值有差异的用点：' + diff.length + ' 处');
for(const d of diff) console.log('  [' + d.k + ']  ' + d.before + '   →   ' + d.after
  + '      （原文：' + d.beforeRaw + ' → ' + d.afterRaw + '）');
if(onlyH.length) console.log('只在改前存在（被删/改名）：' + onlyH.map(p => p.key).join(' | '));
if(onlyN.length) console.log('只在改后存在（新增）：' + onlyN.map(p => p.key).join(' | '));
process.exit(diff.length ? 1 : 0);   // 有差异就非零退出：可以直接进流水线当门禁
