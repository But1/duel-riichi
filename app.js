/* Presentation only. The Python engine validates every action and scores hands. */
'use strict';
const $ = id => document.getElementById(id);
const NAMES = ['萬','筒','索'].flatMap(s => Array.from({length:9},(_,i)=>`${i+1}${s}`)).concat(['東','南','西','北','白','發','中']);
const CHINESE = ['一','二','三','四','五','六','七','八','九'];
const format = n => Number(n).toLocaleString('en-US');
const escapeHTML = text => String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state = null, busy = false, selectedTile = null, guesses = [], aiTimer = null, requestId = 0, resultShown = null;
let worker;
const pending = new Map();

function symbol(type) {
  if (type < 9) return `<span class="man-mark">${CHINESE[type]}<b>萬</b></span>`;
  if (type >= 27) {
    if (type === 31) return '<svg viewBox="0 0 40 58" aria-hidden="true"><rect x="7" y="8" width="26" height="41" rx="2" fill="none" stroke="#344e68" stroke-width="2.6"/><rect x="10" y="11" width="20" height="35" fill="none" stroke="#344e68" stroke-width=".7"/></svg>';
    return `<span class="honor-mark ${type===32?'green':type===33?'red':''}">${NAMES[type]}</span>`;
  }
  const n = type%9+1;
  const positions = {
    1:[[20,29]],2:[[20,15],[20,43]],3:[[11,12],[20,29],[29,46]],
    4:[[10,14],[30,14],[10,44],[30,44]],5:[[10,12],[30,12],[20,29],[10,46],[30,46]],
    6:[[10,11],[30,11],[10,29],[30,29],[10,47],[30,47]],
    7:[[8,10],[20,10],[32,10],[11,28],[29,28],[11,47],[29,47]],
    8:[[11,9],[29,9],[11,22],[29,22],[11,36],[29,36],[11,49],[29,49]],
    9:[[8,10],[20,10],[32,10],[8,29],[20,29],[32,29],[8,48],[20,48],[32,48]]
  }[n];
  let marks;
  if(type<18){
    marks=positions.map(([x,y],i)=>{
      const color=n===1?'#246452':(n===5&&i===2)||n===7&&i<3?'#ad4433':n===9&&i>=6?'#a53d30':n===9&&i>=3?'#226753':'#304d60';
      const radius=n===1?13:n===9||n===7?4.5:5.5;
      return `<circle cx="${x}" cy="${y}" r="${radius}" fill="none" stroke="${color}" stroke-width="${n===1?3:2.3}"/><circle cx="${x}" cy="${y}" r="${n===1?7:1.5}" fill="${color}"/>${n===1?'<circle cx="20" cy="29" r="3" fill="#b14f3c"/>':''}`;
    }).join('');
  } else {
    marks=positions.map(([x,y],i)=>{
      const color=n===5&&i===2||n===7&&i<3?'#ac4230':'#23624d';
      const h=n===1?37:n>=8?9:13, w=n===1?10:5;
      return `<rect x="${x-w/2}" y="${y-h/2}" width="${w}" height="${h}" rx="1.8" fill="${color}"/><path d="M${x-w/2-1} ${y}h${w+2}" stroke="${color}" stroke-width="2"/><path d="M${x} ${y-h/2+2}v${h-4}" stroke="#f7f2db" stroke-width=".65"/>`;
    }).join('');
  }
  return `<svg viewBox="0 0 40 58" aria-hidden="true">${marks}</svg>`;
}

function tile(id, options={}) {
  const type = options.type ? id : Math.floor(id/4);
  const back = id === null || options.back;
  const attrs = options.attrs || '';
  const cls = `tile ${back?'back':''} ${options.className||''}`;
  const label=back?'蓋住的牌':NAMES[type];
  if(options.button) return `<button type="button" class="${cls}" aria-label="${label}" title="${label}" ${options.disabled?'disabled':''} ${attrs}>${back?'':symbol(type)}</button>`;
  return `<span class="${cls}" role="img" aria-label="${label}" title="${label}" ${attrs}>${back?'':symbol(type)}</span>`;
}

function notify(message) {
  $('error-message').textContent=message;
  $('error-message').hidden=false;
}
function clearError(){ $('error-message').hidden=true; }

function transport(request) {
  if(window.MAHJONG_BACKEND) {
    return fetch('/api/game', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)}).then(async response=>{
      const data=await response.json();
      if(!response.ok&&!data.error) throw new Error('連線失敗，請稍後重試。');
      return data;
    });
  }
  return new Promise((resolve,reject)=>{
    const id=++requestId;
    const timeout=setTimeout(()=>{pending.delete(id);reject(new Error('載入時間較長。請確認網路後重新整理頁面。'));},90000);
    pending.set(id,{resolve,reject,timeout});
    worker.postMessage({id,request});
  });
}

async function act(action, payload={}) {
  if(busy) return null;
  clearTimeout(aiTimer); clearError(); busy=true;
  document.querySelectorAll('#actions button,#human-hand button,#action-content button').forEach(b=>b.disabled=true);
  try {
    const result=await transport({action,payload,revision:state?.revision});
    if(!result.ok) throw new Error(result.error||'操作失敗。');
    state=result.state;
    selectedTile=null;
    if(action==='guess'||action==='new_match'||action==='next_round') guesses=[];
    $('loading').classList.add('hidden');
    busy=false;
    render();
    scheduleAI();
    return state;
  } catch(error) {
    busy=false;
    if(state) render();
    notify(error.message);
    if(!state){
      $('loading-title').textContent='暫時無法載入牌桌';
      $('loading-message').textContent=error.message;
      if(!$('reload-button')){
        const b=document.createElement('button');b.id='reload-button';b.className='button primary error-retry';b.textContent='重新載入';b.onclick=()=>location.reload();$('loading').append(b);
      }
    }
    return null;
  }
}

function scheduleAI(){
  clearTimeout(aiTimer);
  if(!state?.legal.ai||busy||document.querySelector('dialog[open]')) return;
  aiTimer=setTimeout(()=>act('ai'),state.phase==='B'?800:550);
}

function renderMelds(player) {
  return player.melds.map(m=>`<div class="meld-group"><small>${m.opened?'副露':'暗槓'}</small>${m.tiles.map((t,i)=>tile(t,{back:!m.opened&&(i===0||i===3)})).join('')}</div>`).join('');
}
function renderRiver(player) {
  return player.river.map(r=>tile(r.tile,{back:r.face_down,className:`${r.called?'called':''} ${r.riichi?'riichi-discard':''} ${r.phase==='B'?'b-discard':''}`})).join('');
}

function render(){
  if(!state) return;
  const [human,ai]=state.players, legal=state.legal;
  $('round-label').innerHTML=`第 ${String(state.round).padStart(2,'0')} 局 <span>東風場</span>`;
  const isB=state.phase==='B';
  $('phase-label').textContent=state.phase==='result'?'本局結算':isB?'階段 B · 猜牌對決':'階段 A · 摸打';
  $('turn-label').textContent=state.phase==='result'?(state.match_over?'對局結束':'準備下一局'):isB?`第 ${state.b_cycle} 輪猜牌`:`${state.turn===0?'你的':'AI 的'}回合`;
  $('ai-score').textContent=format(ai.score);$('human-score').textContent=format(human.score);
  $('ai-dealer').textContent=ai.dealer?'東・莊':'南・閒';$('human-dealer').textContent=human.dealer?'東・莊':'南・閒';
  $('ai-detail').textContent=ai.riichi?'立直':state.declarer===1?'已宣告聽牌':ai.closed?'門清 · 以公開資訊判斷':'副露 · 以公開資訊判斷';
  const shape=state.shanten===-1?'牌型完成':state.shanten===0?'聽牌牌型':`${state.shanten} 向聽`;
  $('human-detail').textContent=human.riichi?'立直 · 手牌已固定':state.declarer===0?'已宣告聽牌 · 手牌已固定':`${human.closed?'門清':'副露'} · ${shape}`;
  $('ai-hand').innerHTML=ai.hand?ai.hand.map(t=>tile(t)).join(''):Array.from({length:ai.hand_count},()=>tile(null)).join('');
  let hand=[...human.hand];
  const current=state.phase==='A'?state.drawn:null;
  if(current!==null&&hand.includes(current)) hand=hand.filter(t=>t!==current).concat(current);
  $('human-hand').innerHTML=hand.map(t=>tile(t,{button:true,disabled:!legal.discard||busy,className:`${t===selectedTile?'selected':''} ${t===current?'drawn':''}`,attrs:`data-discard-id="${t}" aria-pressed="${selectedTile===t}"`})).join('');
  $('ai-melds').innerHTML=renderMelds(ai);$('human-melds').innerHTML=renderMelds(human);
  $('ai-river').innerHTML=renderRiver(ai);$('human-river').innerHTML=renderRiver(human);
  $('ai-turns').textContent=`${ai.turns} / 18 巡`;$('human-turns').textContent=`${human.turns} / 18 巡`;
  $('wall-count').textContent=state.wall_count;
  $('pot').textContent=format(state.pot);
  $('dice').textContent=state.dice.map(n=>String.fromCodePoint(0x267f+n)).join(' ');
  $('dice').setAttribute('aria-label',`開局骰子：${state.dice.join('、')}`);
  $('center-phase').textContent=state.phase==='result'?'終':state.phase;
  $('center-title').textContent=state.phase==='result'?'本局結束':isB?'猜牌對決':'摸打階段';
  $('dora-row').innerHTML=state.dora_indicators.map(t=>tile(t)).join('')+Array.from({length:5-state.dora_indicators.length},()=>tile(null)).join('');
  $('dora-help').textContent=`寶牌：${state.dora_types.map(t=>NAMES[t]).join('、')}`;
  $('draw-candidate').innerHTML=state.candidate!==null?`<small>${state.candidate_score?'可以和牌':'本次自摸'}</small>${tile(state.candidate)}`:'';
  const oldEvents=$('event-list').dataset.latest;
  if(String(state.events.at(-1)?.id)!==oldEvents){
    $('event-list').innerHTML=[...state.events].reverse().slice(0,12).map(e=>`<li>${escapeHTML(e.text)}</li>`).join('');
    $('event-list').dataset.latest=String(state.events.at(-1)?.id);
  }
  $('event-count').textContent=`第 ${state.round} 局`;
  renderActions();
  if(state.result) {
    const key=`${state.round}-${state.revision}`;
    if(resultShown!==key){resultShown=key;renderResult();$('result-dialog').showModal();}
  }
}

function button(text,action,style='primary',payload=null,disabled=false){
  return `<button type="button" class="button ${style}" data-action="${action}" ${payload!==null?`data-payload="${escapeHTML(JSON.stringify(payload))}"`:''} ${disabled?'disabled':''}>${text}</button>`;
}
function waitBox(){
  return state.waits.length?`<div class="waits-box"><small>你的聽口</small><div class="waits-tiles">${state.waits.map(t=>tile(t,{type:true})).join('')}</div></div>`:'';
}
function setAction(overline,title,description,content,buttons){
  $('action-overline').textContent=overline;$('action-heading').textContent=title;
  $('action-description').textContent=description;$('action-content').innerHTML=content;
  $('actions').innerHTML=buttons;$('step-counter').textContent=state.phase==='result'?'終':state.phase;
}

function renderActions(){
  const s=state,l=s.legal;
  if(s.phase==='result'){
    setAction('本局結束',s.result.kind==='win'?(s.result.winner===0?'你和牌了':'AI 和牌了'):s.result.kind==='blocked'?'成功猜中聽口':'本局流局',s.result.message,'',button('查看結算','show_result','secondary')+button(s.match_over?'再挑戰一場':'開始下一局',s.match_over?'new_match':'next_round'));
    return;
  }
  if(l.ai){
    const thinking=s.phase==='B'?(s.step==='guess'?'AI 正在推測你的聽口。':s.step==='b_review'?'AI 正在確認這次自摸。':'AI 正在自摸。'):'AI 正在判斷向聽數與進張。';
    const desc=s.phase==='B'&&s.declarer===0?'手牌已固定，等待對手猜牌。':s.phase==='B'?'你可以觀察對手的摸切，準備下一輪猜牌。':'你可以先觀察對手的牌河與寶牌。';
    setAction('對手回合',thinking,desc,`<div class="thinking" aria-label="AI 思考中"><i></i><i></i><i></i></div>${s.declarer===0?waitBox():''}`,'');
    return;
  }
  if(l.discard){
    let buttons='';
    if(l.win) buttons+=button(`自摸和牌 · ${format(s.candidate_score.points)} 點`,'win','win');
    buttons+=button(selectedTile===null?'選擇一張牌':`打出 ${NAMES[Math.floor(selectedTile/4)]}`,'discard','primary',{tile:selectedTile},selectedTile===null);
    for(let i=0;i<(l.kans||[]).length;i++) buttons+=button(l.kans[i].label,'kan','secondary',{index:i});
    setAction('輪到你','選擇一張牌打出','點選手牌，再按「打出」。也可以雙擊手牌直接打出。','',buttons);
    return;
  }
  if(l.tenpai){
    setAction('你已經聽牌','要開始猜牌對決嗎？','宣告後立即進入 B 階段，手牌與聽口將固定。',waitBox(),button('宣告聽牌','tenpai')+(l.riichi?button('立直 · 支付 1,000 點','riichi','secondary'):'')+button('繼續摸打','continue','quiet'));
    return;
  }
  if(l.pass){
    let buttons=l.ron?button('榮和','ron','win'):'';
    (l.calls||[]).forEach((c,i)=>buttons+=button(`${c.label}<span class="call-tiles">${c.tiles.map(t=>tile(t)).join('')}</span>`,'call','secondary call-action',{index:i}));
    buttons+=button('略過','pass',l.ron||l.calls?.length?'quiet':'primary');
    setAction('回應對手',s.step==='kan_response'?'可以搶槓和牌':s.last_discard?`對手打出 ${NAMES[Math.floor(s.last_discard.tile/4)]}`:'選擇你的回應',s.step==='kan_response'?'你可以榮和，或讓對手完成槓與補牌。':'可以選擇鳴牌、和牌，或略過並繼續摸牌。','',buttons);
    return;
  }
  if(l.guess){
    const grid=Array.from({length:4},(_,suit)=>`<div class="guess-suit">${Array.from({length:suit===3?7:9},(_,i)=>suit*9+i).map(t=>tile(t,{type:true,button:true,className:guesses.includes(t)?'selected':'',attrs:`data-guess="${t}" aria-pressed="${guesses.includes(t)}"`})).join('')}</div>`).join('');
    const slots=[0,1].map(i=>`<span class="guess-slot">${guesses[i]===undefined?'？':tile(guesses[i],{type:true})}</span>`).join('');
    const history=s.guess_history.length?`<p class="guess-history">上輪：${s.guess_history.at(-1).tiles.map(t=>NAMES[t]).join('、')}，未命中</p>`:'';
    setAction(`第 ${s.b_cycle} 輪 · 由你猜牌`,'指出兩種和牌','觀察對手的牌河與副露。任一種命中，就能阻止對手和牌。',`<div class="guess-grid">${grid}</div><div class="guess-label"><span>你的選擇</span><span>${guesses.length} / 2</span></div><div class="selected-guesses">${slots}</div>${history}`,button('確認猜牌','guess','primary',{tiles:guesses},guesses.length!==2));
    return;
  }
  if(l.draw||l.skip){
    const score=s.candidate_score,remaining=s.b_left;
    const progress=`<div class="draw-progress" aria-label="本輪剩餘 ${remaining} 次">${Array.from({length:5},(_,i)=>`<span class="${i<5-remaining?'done':''}"></span>`).join('')}</div>`;
    const lastGuesses=s.guesses.map(t=>NAMES[t]).join('、');
    let title,desc,buttons,extra='';
    if(l.draw){title='對手沒有猜中';desc=`對手猜了 ${lastGuesses}。本輪還可自摸 ${remaining} 次。`;buttons=button('自摸一張','draw');}
    else if(score){title='這張可以和牌';desc='可以收下這次和牌，或蓋牌放過，繼續追求更高點數。'+(s.players[0].riichi?' 預估點數未含裏寶牌。':'');buttons=button(`和牌 · ${format(score.points)} 點${s.players[0].riichi?'起':''}`,'win','win')+button('蓋牌，繼續自摸','skip_next','secondary');extra=`<div class="value-preview">${format(score.points)}<small>${score.han} 番 ${score.fu} 符</small></div>`;}
    else {title=`摸到 ${NAMES[Math.floor(s.candidate/4)]}`;desc=remaining>0?`未能和牌。本輪還有 ${remaining} 次自摸。`:'本輪自摸已用完，打出此牌後將再次猜牌。';buttons=button(remaining>0?'摸切並繼續':'摸切，結束本輪','skip_next');}
    setAction(`第 ${s.b_cycle} 輪 · 你的自摸`,title,desc,progress+extra+waitBox(),buttons);
  }
}

function renderResult(){
  const r=state.result,win=r.kind==='win',p=win?r.winner:state.declarer;
  let html=`<div class="result-symbol">${win?'和':r.kind==='blocked'?'見':'流'}</div><span class="eyebrow">第 ${state.round} 局 · ${win?(r.score.tsumo?'自摸':'榮和'):'結算'}</span><h2>${win?(r.winner===0?'你和牌了':'AI 和牌了'):r.kind==='blocked'?'聽口被猜中了':'本局流局'}</h2><p class="result-description">${escapeHTML(r.message)}</p>`;
  if(state.match_over) html+=`<div class="match-win">${r.match_winner===0?'你贏得了這場對局！':'AI 贏得了這場對局。'}</div>`;
  if(win){
    const s=r.score;
    html+=`<div class="result-score">${format(s.points)} <small>點</small></div><div class="result-fu">${s.han} 番 ${s.fu} 符${s.limit?' · '+s.limit:''}${s.dealer?' · 莊家':''}</div>`;
    const player=state.players[p];
    html+=`<div class="result-tiles">${player.hand.map(t=>tile(t,{className:t===r.tile?'wait-highlight':''})).join('')}${player.melds.map(m=>'<span style="width:6px"></span>'+m.tiles.map(t=>tile(t)).join('')).join('')}</div><div class="yaku-list">${s.yaku.map(y=>`<div>${escapeHTML(y.name)}<span>${y.han} 番</span></div>`).join('')}${r.pot?`<div>立直供託<span>+ ${format(r.pot)} 點</span></div>`:''}</div>`;
    if(r.ura_indicators?.length)html+=`<p class="result-description">裏寶牌指示牌</p><div class="result-tiles">${r.ura_indicators.map(t=>tile(t)).join('')}</div>`;
  } else if(r.waits?.length){
    html+=`<p class="result-description" style="margin-top:20px">本局聽口</p><div class="result-tiles">${r.waits.map(t=>tile(t,{type:true})).join('')}</div>`;
    if(p!==null)html+=`<p class="result-description">${p===0?'你的':'AI 的'}手牌</p><div class="result-tiles">${state.players[p].hand.map(t=>tile(t)).join('')}</div>`;
  }
  html+=`<div class="score-summary"><div>你<strong>${format(state.players[0].score)}</strong></div><div>AI<strong>${format(state.players[1].score)}</strong></div></div>`;
  if(!state.match_over)html+=`<p class="result-description" style="margin-top:16px">下局由${state.dealer===0?'你':' AI '}坐莊${state.pot?` · 供託 ${format(state.pot)} 點保留`:''}</p>`;
  $('result-content').innerHTML=html;
  $('result-actions').innerHTML=button('返回牌桌','close_result','secondary')+button(state.match_over?'再挑戰一場':'開始下一局',state.match_over?'new_match':'next_round');
}

document.addEventListener('click',async e=>{
  const hand=e.target.closest('[data-discard-id]');
  if(hand&&!busy&&state?.legal.discard){selectedTile=Number(hand.dataset.discardId);document.querySelectorAll('[data-discard-id]').forEach(b=>{b.classList.toggle('selected',Number(b.dataset.discardId)===selectedTile);b.setAttribute('aria-pressed',String(Number(b.dataset.discardId)===selectedTile));});renderActions();return;}
  const guess=e.target.closest('[data-guess]');
  if(guess&&!busy&&state?.legal.guess){
    const t=Number(guess.dataset.guess);clearError();
    if(guesses.includes(t)) guesses=guesses.filter(x=>x!==t);
    else if(guesses.length<2)guesses.push(t);
    else {notify('已選兩種牌。請先取消其中一種，再選另一種。');return;}
    renderActions();document.querySelector(`[data-guess="${t}"]`)?.focus({preventScroll:true});return;
  }
  const b=e.target.closest('[data-action]');
  if(!b||b.disabled||busy)return;
  const action=b.dataset.action;
  if(action==='show_result'){renderResult();$('result-dialog').showModal();return;}
  if(action==='close_result'){$('result-dialog').close();return;}
  if(action==='skip_next'){
    const updated=await act('skip');
    if(updated?.legal.draw) await act('draw');
    return;
  }
  if(action==='next_round'||action==='new_match')$('result-dialog').close();
  await act(action,b.dataset.payload?JSON.parse(b.dataset.payload):{});
});
document.addEventListener('dblclick',e=>{
  const t=e.target.closest('[data-discard-id]');
  if(t&&!busy&&state?.legal.discard)act('discard',{tile:Number(t.dataset.discardId)});
});
$('rules-button').onclick=()=>{clearTimeout(aiTimer);$('rules-dialog').showModal();};
$('reset-button').onclick=()=>{clearTimeout(aiTimer);$('reset-dialog').showModal();};
$('cancel-reset').onclick=()=>$('reset-dialog').close();
$('confirm-reset').onclick=()=>{if(busy)return;$('reset-dialog').close();resultShown=null;act('new_match');};
document.querySelectorAll('dialog').forEach(d=>d.addEventListener('close',scheduleAI));
document.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!document.querySelector('dialog[open]')&&!busy&&state?.legal.discard&&selectedTile!==null&&!e.target.closest('button'))act('discard',{tile:selectedTile});
});

function registerTools(){
  const context=document.modelContext;
  if(!context?.registerTool)return;
  const lifecycle=new AbortController();
  const register=tool=>{try{Promise.resolve(context.registerTool(tool,{signal:lifecycle.signal})).catch(()=>{});}catch{}};
  register({name:'read_mahjong_table',title:'讀取麻將牌桌',description:'Read the visible table and legal actions without revealing the AI concealed hand or wall.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:false},execute:()=>({phase:state?.phase,step:state?.step,legal:state?.legal,players:state?.players,waits:state?.waits,revision:state?.revision})});
  register({name:'play_mahjong_action',title:'執行麻將操作',description:'Perform a legal human game action and update the visible table. Does not start a new match or automatically play the AI.',inputSchema:{type:'object',properties:{action:{type:'string',enum:['discard','tenpai','riichi','continue','pass','call','kan','guess','draw','skip','win','ron','next_round']},payload:{type:'object'}},required:['action'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:false},execute:async input=>{const allowed=['discard','tenpai','riichi','continue','pass','call','kan','guess','draw','skip','win','ron','next_round'];if(!input||!allowed.includes(input.action)||busy)throw new Error('Invalid action or game is busy');if(input.action==='next_round')$('result-dialog').close();const updated=await act(input.action,input.payload||{});if(!updated)throw new Error($('error-message').textContent);return{phase:updated.phase,step:updated.step,legal:updated.legal,revision:updated.revision};}});
  window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});
}

async function initialize(){
  if(!window.MAHJONG_BACKEND){
    worker=new Worker('./worker.js');
    worker.onmessage=event=>{
      if(event.data.type==='progress'){$('loading-message').textContent=event.data.message;return;}
      const item=pending.get(event.data.id);
      if(item){clearTimeout(item.timeout);pending.delete(event.data.id);item.resolve(event.data);}
    };
    worker.onerror=error=>{for(const item of pending.values()){clearTimeout(item.timeout);item.reject(new Error(error.message||'Python 規則引擎載入失敗。'));}pending.clear();};
  }
  await act(window.MAHJONG_BACKEND?'state':'new_match');
  registerTools();
}
initialize();
