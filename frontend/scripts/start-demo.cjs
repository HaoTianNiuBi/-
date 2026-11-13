const { execFile, spawn } = require('node:child_process');
const { existsSync, mkdirSync, openSync, closeSync } = require('node:fs');
const path = require('node:path');
const http = require('node:http');

const root = path.resolve(__dirname, '..');
const url = 'http://127.0.0.1:5173/';
const vite = path.join(root, 'node_modules', 'vite', 'bin', 'vite.js');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function apiStatus() {
  return new Promise(resolve => {
    const req = http.get('http://127.0.0.1:8765/api/health', res => {
      let body=''; res.on('data', chunk => {body+=chunk});
      res.on('end',()=>{try{resolve(JSON.parse(body).version==='0.6.0'?'ready':'occupied')}catch{resolve('occupied')}});
      res.on('error',()=>resolve('offline'));
    });
    req.setTimeout(1500,()=>req.destroy()); req.on('error',()=>resolve('offline'));
  });
}

async function startBackend() {
  const initial=await apiStatus();
  if(initial==='ready')return;
  if(initial==='occupied')throw new Error('Port 8765 belongs to another application. No process was stopped.');
  const backend=path.resolve(root,'../backend');
  const python=path.join(backend,'.venv','Scripts','python.exe');
  if(!existsSync(python))throw new Error('Backend environment missing. See backend/README.md.');
  const logDir=path.join(root,'.demo');mkdirSync(logDir,{recursive:true});
  const log=openSync(path.join(logDir,'backend.log'),'a');
  const child=spawn(python,['-m','uvicorn','main:app','--host','127.0.0.1','--port','8765'],{cwd:backend,detached:true,windowsHide:true,stdio:['ignore',log,log]});
  closeSync(log);child.on('error',error=>console.error(error.message));child.unref();
  for(let i=0;i<60;i++){await delay(250);if(await apiStatus()==='ready')return;}
  throw new Error('Backend did not start. See frontend/.demo/backend.log.');
}

function check() {
  return new Promise(resolve => {
    const request = http.get(url, response => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', chunk => { body += chunk; });
      response.on('end', () => resolve(response.statusCode === 200 && body.includes('你的私人空间') ? 'ready' : 'occupied'));
      response.on('error', () => resolve('offline'));
    });
    request.setTimeout(1500, () => request.destroy());
    request.on('error', () => resolve('offline'));
  });
}

async function main() {
  await startBackend();
  let status = await check();
  if (status === 'occupied') throw new Error('Port 5173 is used by another application. No process was stopped.');
  if (status !== 'ready') {
    if (!existsSync(vite)) throw new Error('Dependencies missing. Run npm ci in the frontend folder first.');
    const logDir = path.join(root, '.demo');
    mkdirSync(logDir, { recursive: true });
    const log = openSync(path.join(logDir, 'server.log'), 'a');
    const child = spawn(process.execPath, [vite, '--host', '127.0.0.1', '--port', '5173', '--strictPort'], {
      cwd: root, detached: true, windowsHide: true, stdio: ['ignore', log, log],
    });
    closeSync(log);
    child.on('error', error => { console.error(error.message); process.exitCode = 1; });
    child.unref();
    for (let attempt = 0; attempt < 40; attempt++) {
      await delay(250);
      status = await check();
      if (status === 'ready') break;
      if (status === 'occupied') throw new Error('Port 5173 is occupied. See frontend/.demo/server.log.');
    }
    if (status !== 'ready') throw new Error('Demo did not start. See frontend/.demo/server.log.');
  }
  console.log(`Demo ready: ${url}`);
  if (!process.argv.includes('--no-open')) {
    await openBrowser();
  }
}

function openBrowser() {
  return new Promise((resolve, reject) => {
    // Wait for Windows to hand the URL to the default browser before exiting.
    const shell = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
    const command = `Start-Process -FilePath '${url}' -ErrorAction Stop`;
    execFile(shell, ['-NoProfile', '-NonInteractive', '-Command', command],
      { windowsHide: true, timeout: 15000 }, error => {
        if (error) reject(new Error(`Could not open the default browser. Open this URL manually: ${url}`));
        else resolve();
      });
  });
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
