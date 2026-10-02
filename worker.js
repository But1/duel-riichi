/* Every game decision is evaluated by the shared Python engine. */
importScripts('./vendor/pyodide.js');
let engine;
const ready = (async () => {
  postMessage({type: 'progress', message: '正在載入 Python 規則引擎…'});
  const py = await loadPyodide({indexURL: './vendor/'});
  const [library, source] = await Promise.all([
    fetch('./python/mahjong.zip').then(r => {if (!r.ok) throw new Error('計分套件載入失敗'); return r.arrayBuffer();}),
    fetch('./python/engine.py').then(r => {if (!r.ok) throw new Error('規則引擎載入失敗'); return r.text();})
  ]);
  py.unpackArchive(library, 'zip', {extractDir: '/home/pyodide'});
  py.runPython(source);
  engine = py.globals.get('dispatch_json');
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
