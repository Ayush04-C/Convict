/* Real browser + isolated FastAPI pipeline. No external provider calls. */
const assert = require('node:assert/strict');
const {spawn, spawnSync} = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {chromium} = require('playwright');
async function main(){
  const reservation=net.createServer();await new Promise(resolve=>reservation.listen(0,'127.0.0.1',resolve));const port=reservation.address().port;await new Promise(resolve=>reservation.close(resolve));
  const python=process.env.LEO_PYTHON||'python';
  const server=spawn(python,['-m','tests.web_server',String(port)],{stdio:['ignore','pipe','pipe']});
  let logs='';server.stdout.on('data',data=>logs+=data);server.stderr.on('data',data=>logs+=data);
  let browser;const origin='http://127.0.0.1:'+port;const directory=await fs.mkdtemp(path.join(os.tmpdir(),'leo-ui-test-'));
  try{
    let ready=false;
    for(let i=0;i<100;i++){try{if((await fetch(origin+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)throw new Error('Test server stopped: '+logs);await new Promise(resolve=>setTimeout(resolve,100));}
    assert(ready,'Test server did not start: '+logs);
    browser=await chromium.launch({headless:true,...(process.env.LEO_BROWSER_CHANNEL?{channel:process.env.LEO_BROWSER_CHANNEL}:{})});
    const page=await browser.newPage({viewport:{width:1200,height:950}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(origin);await page.getByText('Demo · synthetic data',{exact:true}).waitFor();
    assert.equal((await page.request.get(origin+'/runs/private/report')).status(),401);
    await page.getByLabel('Username',{exact:true}).fill('browser-user');await page.getByLabel('Password',{exact:true}).fill('incorrect-password');await page.getByRole('button',{name:'Sign in'}).click();
    await page.getByRole('alert').filter({hasText:'Please sign in again'}).waitFor();
    await page.getByLabel('Password',{exact:true}).fill('browser-test-password-42');await page.getByRole('button',{name:'Sign in'}).click();await page.locator('#workspace').waitFor({state:'visible'});
    await page.getByLabel('Company name',{exact:true}).fill('Synthetic');await page.locator('[name=domain]').fill('example.com');
    await page.locator('#deck').setInputFiles({name:'empty.pdf',mimeType:'application/pdf',buffer:Buffer.alloc(0)});await page.getByRole('button',{name:'Review this deck'}).click();await page.getByRole('alert').filter({hasText:'nonempty PDF'}).waitFor();
    const pdf=spawnSync(python,['-c','from tests.pipeline_support import pdf_bytes; import sys; sys.stdout.buffer.write(pdf_bytes())']);assert.equal(pdf.status,0);
    await page.locator('#deck').setInputFiles({name:'synthetic.pdf',mimeType:'application/pdf',buffer:pdf.stdout});await page.getByRole('button',{name:'Review this deck'}).click();
    await page.locator('.outcome').filter({hasText:'DO NOT INVEST'}).waitFor({timeout:30000});
    assert.equal(await page.locator('#spinner').isVisible(),false);assert.equal(await page.locator('.agent-list li').count(),4);
    const runId=await page.locator('#run-id').inputValue();assert(runId);
    const download=page.waitForEvent('download');await page.getByRole('button',{name:'Download Markdown'}).click();const md=await download;const filename=path.join(directory,'report.md');await md.saveAs(filename);const contents=await fs.readFile(filename,'utf8');assert(contents.includes('DO NOT INVEST'));assert(contents.includes('more than three times'));
    const jsonDownload=page.waitForEvent('download');await page.getByRole('button',{name:'Download JSON'}).click();const jsonFile=path.join(directory,'report.json');await (await jsonDownload).saveAs(jsonFile);const rawReport=JSON.parse(await fs.readFile(jsonFile,'utf8'));assert.equal(rawReport.verdict_label,'DO NOT INVEST');assert.equal(rawReport.run_id,runId);assert(rawReport.evidence.some(e=>e.display_label==='Upper revenue estimate'));
    await page.getByRole('link',{name:'Source 2',exact:true}).first().click();assert(await page.locator('#source-2').isVisible());
    assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    if(process.env.LEO_WEB_SCREENSHOT)await page.screenshot({path:process.env.LEO_WEB_SCREENSHOT,fullPage:true});
    await page.locator('#run-id').fill('missing-review');await page.getByRole('button',{name:'Open report'}).click();await page.getByRole('alert').filter({hasText:'Run does not exist'}).waitFor();
    await page.locator('#run-id').fill(runId);await page.getByRole('button',{name:'Open report'}).click();await page.locator('.outcome').waitFor({state:'visible'});
    await page.route('**/runs/*/report',async route=>{const response=await route.fetch();const report=await response.json();report.claims[0].findings[0].note='<img src=x onerror="window.injection=true">';await route.fulfill({json:report});});
    await page.getByRole('button',{name:'Open report'}).click();await page.getByText('<img src=x onerror="window.injection=true">',{exact:true}).waitFor();assert.equal(await page.locator('#report-content img').count(),0);assert.equal(await page.evaluate(()=>window.injection),undefined);
    await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.getByRole('button',{name:'Sign out'}).click();assert.equal(await page.locator('#workspace').isVisible(),false);assert.equal(await page.locator('#report-content').textContent(),'');assert.deepEqual(errors,[]);
    console.log('PASS: sign-in errors, PDF validation, upload, full offline pipeline, four specialists, report, downloads, resume, missing run, safe text rendering, mobile layout and sign-out.');
  }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,3000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(directory,{recursive:true,force:true});}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
