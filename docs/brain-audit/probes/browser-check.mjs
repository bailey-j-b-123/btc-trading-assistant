// Run with AUDIT_NODE_MODULES pointing to an external tooling package.json.
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
const require = createRequire(process.env.AUDIT_NODE_MODULES || '/home/user/audit-browser/package.json');
const {default: puppeteer} = await import(require.resolve('puppeteer-core'));
const {default: chromium} = await import(require.resolve('@sparticuz/chromium'));
const axeSource = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
const url = process.argv[2] || 'http://127.0.0.1:8138/';
const out = path.resolve(process.argv[3] || 'docs/brain-audit/evidence/browser-baseline');
fs.mkdirSync(out, {recursive:true});
const browser = await puppeteer.launch({args:[...chromium.args,'--lang=en-US'], env:{...process.env,LANG:'en_US.UTF-8',LC_ALL:'en_US.UTF-8'}, executablePath:await chromium.executablePath(), headless:true});
const report = {url, browser:await browser.version(), provenance:'SYNTHETIC 600-candle seed; NOT market history or approved visual reference', views:[]};
try {
 for(const [width,height] of [[1536,864],[1440,900],[1920,1080],[390,844]]) {
  const page=await browser.newPage();const errors=[],failed=[],blocked=[];
  await page.setRequestInterception(true);
  page.on('request',r=>{if(!r.url().startsWith(new URL(url).origin) && !r.url().startsWith('data:')) {blocked.push(r.url());r.abort();}else r.continue();});
  page.on('pageerror',e=>errors.push(e.message));
  page.on('response',r=>{if(r.status()>=400) failed.push({url:r.url(),status:r.status()});});
  await page.setViewport({width,height,deviceScaleFactor:1});
  await page.goto(url,{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>document.querySelector('.hero-card') && document.querySelector('.terminal-chart-card canvas') && !document.querySelector('[aria-busy="true"]'),{timeout:180000});
  await page.evaluate(()=>document.fonts.ready);
  for(const fn of ['support_resistance','swings','patterns','breakouts','trade_plan']) {
   const selector=`button[data-function="${fn}"]`;
   await page.$eval(selector,n=>n.scrollIntoView({block:'center'}));
   await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
   await page.click(selector);
   await page.waitForFunction((s)=>document.querySelector(s)?.getAttribute('aria-pressed')==='true',{timeout:5000},selector);
   const metrics=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth, status:document.querySelector('.chart-function-status')?.textContent, panel:document.querySelector('.chart-function-panel')?.innerText, canvasCount:document.querySelectorAll('.terminal-chart-card canvas').length}));
   report.views.push({width,height,function:fn,...metrics});
   if((width===1536||width===390)&&fn==='swings') await page.screenshot({path:path.join(out,`swings-${width}.png`),fullPage:true});
  }
  await page.addScriptTag({content:axeSource});
  const axe=await page.evaluate(async()=>{const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return {passes:r.passes.length,incomplete:r.incomplete.map(i=>({id:i.id,nodes:i.nodes.length})),violations:r.violations.map(v=>({id:v.id,impact:v.impact,description:v.description,help:v.help,nodes:v.nodes.map(n=>({target:n.target,html:n.html,failureSummary:n.failureSummary}))}))};});
  report.views.push({width,height,accessibility:axe,pageErrors:errors,httpErrors:failed,externalRequestsBlocked:blocked});
  await page.close();
 }
} finally {await browser.close();fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));}
console.log(JSON.stringify(report.views.map(v=>v.accessibility?{width:v.width,axe:v.accessibility.violations.map(x=>({id:x.id,nodes:x.nodes.length})),errors:v.pageErrors,http:v.httpErrors}:{width:v.width,function:v.function,overflow:v.scrollWidth>v.width,status:v.status}),null,2));
