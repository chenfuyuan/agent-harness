#!/usr/bin/env node
/**
 * 零模板绑定的通用原型检查器（Template-Free Prototype Checker）
 *
 * 用法：
 *   node check_prototype.mjs <原型目录或具体 .html 文件> [--shots <截图输出目录>] [--all]
 *
 * 特点：
 * - 不假设任何固定菜单结构（兼容全屏座舱、无限画布、命令流或单栏页面）；
 * - 传入目录时：若有 index.html 默认测入口（传 `--all` 则测全部 HTML）；若处于主屏比稿期（尚无 index.html，如 variant-a/b.html），自动检查目录下所有 .html 文件；
 * - 若页面挂载了 dev-overlay.js，自动遍历所有注册的 `views × states` 组合；同时检查本地资源与站内锚点断链；
 * - 硬错误（返回码 1）：JS 运行期异常、空页面崩溃、断掉的本地静态资源或站内锚点；
 * - 务实可用性提醒（不卡 2px 像素细节）：视口级严重横向溢出、控制台 console.error、明显冗余的框套框嵌套；
 * - 带 `--shots` 时自动隐藏悬浮胶囊输出纯净全页截图，供人眼与 AI 进行审美 `distill` 检视。
 */
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const rawArgs = process.argv.slice(2);
let shotsDir = '';
let checkAllHtml = false;
const positional = [];

for (let i = 0; i < rawArgs.length; i += 1) {
  if (rawArgs[i] === '--shots') {
    shotsDir = rawArgs[i + 1] || '';
    i += 1;
  } else if (rawArgs[i] === '--all') {
    checkAllHtml = true;
  } else {
    positional.push(rawArgs[i]);
  }
}

const targetArg = positional[0];
if (!targetArg) {
  console.error('用法：node check_prototype.mjs <原型目录或 .html 文件> [--shots <截图目录>] [--all]');
  process.exit(2);
}

const resolvedTarget = path.resolve(targetArg);
if (!fs.existsSync(resolvedTarget)) {
  console.error(`找不到目标路径：${resolvedTarget}`);
  process.exit(2);
}

let htmlEntries = [];
if (fs.statSync(resolvedTarget).isDirectory()) {
  const indexCandidate = path.join(resolvedTarget, 'index.html');
  const allHtmls = fs.readdirSync(resolvedTarget)
    .filter((f) => f.toLowerCase().endsWith('.html'))
    .sort()
    .map((f) => path.join(resolvedTarget, f));

  if (!checkAllHtml && fs.existsSync(indexCandidate)) {
    htmlEntries = [indexCandidate];
  } else {
    htmlEntries = allHtmls;
  }
} else {
  htmlEntries = [resolvedTarget];
}

if (htmlEntries.length === 0) {
  console.error(`在 ${resolvedTarget} 中未找到任何 .html 文件`);
  process.exit(2);
}

if (shotsDir) fs.mkdirSync(shotsDir, { recursive: true });

function resolveChromeExecutable() {
  if (process.env.CHROME && fs.existsSync(process.env.CHROME)) return process.env.CHROME;
  const candidates = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
  ];
  return candidates.find((p) => fs.existsSync(p)) || candidates[0];
}

const chromePath = resolveChromeExecutable();
let browser;
try {
  browser = await chromium.launch({
    executablePath: chromePath,
    headless: true,
    args: ['--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage'],
  });
} catch (err) {
  console.error(`无法启动无头浏览器 (${chromePath})：${err.message}`);
  console.error('若在受限终端沙盒内运行，请允许该浏览器自检命令在沙盒外执行或检查 CHROME 路径。');
  process.exit(2);
}

const problems = [];
const warnings = [];
let totalCombos = 0;

for (const entryHtml of htmlEntries) {
  const fileLabel = path.basename(entryHtml);
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  page.on('pageerror', (err) => {
    problems.push(`[${fileLabel}] 运行期脚本异常：${err.message}`);
  });
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      warnings.push(`[${fileLabel}] console.error: ${msg.text()}`);
    }
  });

  const baseUrl = pathToFileURL(entryHtml).href;
  await page.goto(baseUrl, { waitUntil: 'load' });
  await page.waitForTimeout(200);

  // 检查本地静态资源（script/link/img/a）与站内锚点断链
  const linkAudit = await page.evaluate(() => {
    const localUrls = [];
    const brokenAnchors = [];
    document.querySelectorAll('script[src], link[rel="stylesheet"][href], img[src], a[href]').forEach((el) => {
      const raw = (el.getAttribute('src') || el.getAttribute('href') || '').trim();
      if (!raw || /^(https?:|mailto:|tel:|javascript:|data:|blob:)/i.test(raw)) return;
      if (raw.startsWith('#')) {
        if (raw.length > 1 && !raw.startsWith('#?')) {
          const id = decodeURIComponent(raw.slice(1));
          if (!document.getElementById(id) && !document.querySelector(`[name="${CSS.escape(id)}"]`)) {
            brokenAnchors.push(raw);
          }
        }
        return;
      }
      localUrls.push(raw.split('#')[0].split('?')[0]);
    });
    return { localUrls, brokenAnchors };
  });

  for (const anchor of linkAudit.brokenAnchors) {
    problems.push(`[${fileLabel}] 站内锚点目标不存在：${anchor}`);
  }

  for (const rel of linkAudit.localUrls) {
    if (!rel) continue;
    const assetUrl = new URL(rel, baseUrl);
    if (assetUrl.protocol === 'file:') {
      const p = decodeURIComponent(assetUrl.pathname);
      if (!fs.existsSync(p)) {
        problems.push(`[${fileLabel}] 本地资源或链接文件缺失：${rel} (${p})`);
      }
    }
  }

  const matrix = await page.evaluate(() => {
    if (window.__DEV_OVERLAY__ && typeof window.__DEV_OVERLAY__.getMatrix === 'function') {
      return window.__DEV_OVERLAY__.getMatrix();
    }
    return { views: ['default'], states: ['default'], specs: [] };
  });

  for (const v of matrix.views) {
    for (const s of matrix.states) {
      totalCombos += 1;
      const tag = `${fileLabel}:${v}/${s}`;
      await page.evaluate(({ view, state }) => {
        if (window.__DEV_OVERLAY__) window.__DEV_OVERLAY__.setViewState(view, state);
      }, { view: v, state: s });
      await page.waitForTimeout(120);

      const check = await page.evaluate(() => {
        const root = document.querySelector('main, #app') || document.body;
        const textLen = (root.innerText || '').trim().length;
        const visibleChildren = Array.from(root.children).filter(
          (el) => el.id !== '__proto_dev_overlay__' && el.getClientRects().length > 0
        ).length;
        const overflow = document.documentElement.scrollWidth > window.innerWidth + 4
          ? `${document.documentElement.scrollWidth}px > ${window.innerWidth}px`
          : '';

        // 务实防廉价感提醒：使用 getClientRects() 兼顾 position: fixed 座舱面板，检测双重实线框套框
        let nestedBorderCards = 0;
        root.querySelectorAll('[class*="card"] [class*="card"], [class*="panel"] [class*="panel"]').forEach((el) => {
          if (el.getClientRects().length === 0) return;
          const parentCard = el.parentElement?.closest('[class*="card"], [class*="panel"]');
          if (!parentCard || parentCard.getClientRects().length === 0) return;
          const cs = getComputedStyle(el);
          const pcs = getComputedStyle(parentCard);
          if (parseFloat(cs.borderTopWidth) >= 1 && parseFloat(pcs.borderTopWidth) >= 1) {
            nestedBorderCards += 1;
          }
        });

        return { empty: textLen === 0 && visibleChildren === 0, overflow, nestedBorderCards };
      });

      if (check.empty) {
        problems.push(`[${tag}] 页面主内容为空白`);
      }
      if (check.overflow) {
        warnings.push(`[${tag}] 视口出现未预期的横向溢出（${check.overflow}）`);
      }
      if (check.nestedBorderCards > 2) {
        warnings.push(`[${tag}] 检测到 ${check.nestedBorderCards} 处带边框容器层层套框，建议改用背景色阶或细分割线降噪`);
      }

      if (shotsDir) {
        await page.evaluate(() => {
          if (window.__DEV_OVERLAY__) window.__DEV_OVERLAY__.setHidden(true);
        });
        const baseName = path.basename(entryHtml, '.html');
        const shotFile = path.join(shotsDir, `${baseName}__${v}__${s}.png`.replace(/[^a-zA-Z0-9_.-]/g, '_'));
        await page.screenshot({ path: shotFile, fullPage: true });
        await page.evaluate(() => {
          if (window.__DEV_OVERLAY__) window.__DEV_OVERLAY__.setHidden(false);
        });
      }
    }
  }

  await page.close();
}

await browser.close();

const summaryTarget = htmlEntries.length === 1 ? path.basename(htmlEntries[0]) : `${htmlEntries.length} 个 HTML 文件`;
console.log(`原型检查完成（${summaryTarget}）：遍历 ${totalCombos} 个视图×状态组合，问题 ${problems.length} 个，提醒 ${warnings.length} 个。`);
problems.forEach((p) => console.error(`  [问题] ${p}`));
warnings.forEach((w) => console.log(`  [提醒] ${w}`));
process.exit(problems.length ? 1 : 0);
