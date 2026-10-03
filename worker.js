/* Every game decision is evaluated by the shared Python engine. */
importScripts('./vendor/pyodide.js');
let engine;
const ready = (async () => {
  postMessage({type: 'progress', message: '正在準備執行環境，首次需載入約 12 MB 的程式檔案…'});
  const py = await loadPyodide({indexURL: './vendor/'});
  postMessage({type: 'progress', message: '執行環境已就緒，正在載入牌局規則…'});
  const [library, source] = await Promise.all([
    fetch('./python/mahjong.zip').then(r => {if (!r.ok) throw new Error('計分套件載入失敗'); return r.arrayBuffer();}),
    fetch('./python/engine.py?v=3').then(r => {if (!r.ok) throw new Error('規則引擎載入失敗'); return r.text();})
  ]);
  py.unpackArchive(library, 'zip', {extractDir: '/home/pyodide'});
  py.runPython(source);
  engine = py.globals.get('dispatch_json');
  postMessage({type: 'progress', message: '牌桌已就緒，選擇局數後即可開始。'});
  return py;
})();
self.onmessage = async event => {
  const {id, request} = event.data;
  try {
    await ready;
    const result = JSON.parse(engine(JSON.stringify(request)));
    postMessage({id, ...result});
  } catch (error) {
    postMessage({id, ok: false, error: error.message || String(error)});
  }
};
