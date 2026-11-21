// ev_range_test.js — 纯电续航非null回归（v9.7.52）
// 修 ID.AURA T6 等车"有效续航 null km"：根因=补采电量藏文本 key_configs 里，battery_kwh 字段空。
// 断言：①所有纯电车 kwh>0；②rangeCalc 对所有纯电返回非null有效续航；③页面无"null km"/"undefined"字面。
// 由 midsuv_full_regress.py 调用。退出码0=全过。
const fs=require('fs');
const html=fs.readFileSync(process.argv[2],'utf8');
let pass=0,fail=[];
function ok(n,c,m){if(c){pass++;}else{fail.push(n);}console.log((c?'PASS':'FAIL')+'  '+n+(m?' :: '+m:''));}

// 1) 静态：页面无 null km 字面（typeof x!=='undefined' 是合法防御写法，不算）
ok('页面无"null km"', !html.includes('null km'));
ok('渲染层有防御', html.includes('r.eff!=null')&&html.includes('数据待核'));

// 2) 动态：提取 CARS + rangeCalc + 默认 cond
const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]).join('\n');
const els={};
function mkEl(id){return {id,innerHTML:'',value:'',textContent:'',style:{},children:[],tag:'div',className:'',dataset:{},
  addEventListener(){},appendChild(c){this.children.push(c);},classList:{add(){},remove(){}},querySelectorAll(){return [];},
  querySelector(sel){if(sel==='select'&&this.dataset&&this.dataset.fid)return els['c_'+this.dataset.fid]||mkEl('sub');return this._s||(this._s=mkEl('s'));},
  getAttribute(){return null;},setAttribute(){}};}
global.document={getElementById(id){return els[id]||(els[id]=mkEl(id));},createElement(t){const e=mkEl('new');e.tag=t;return e;},querySelectorAll(){return [];},querySelector(){return mkEl('q');}};
global.window=global;
els.pLow=mkEl('pLow');els.pLow.value='12';els.pHigh=mkEl('pHigh');els.pHigh.value='18';
els.bodyFilter=mkEl('bodyFilter');els.bodyFilter.value='SUV';els.wEnergy=mkEl('wEnergy');els.wEnergy.value='100';
const fm=html.match(/const FACTORS=(\{[\s\S]*?\});\s*const FACTOR_GROUPS/);
if(!fm){console.error('FAIL 无法提取FACTORS');process.exit(1);}
const FAC=JSON.parse(fm[1]);for(const[fid,f]of Object.entries(FAC)){els['c_'+fid]=mkEl('c_'+fid);els['c_'+fid].value=f.default;}

// 只 eval 到引擎部分（避免 render 副作用干扰），用暴露方式
const expose=`\n;window.__E={CARS:CARS,rangeCalc:rangeCalc,adjust:adjust};\n`;
let core=scripts;
try{eval(core+expose);}catch(e){console.error('EVAL ERROR',e.message);process.exit(2);}
const EVCARS=window.__E.CARS, evRangeCalc=window.__E.rangeCalc;
const cond={};for(const k of Object.keys(FAC))cond[k]=FAC[k].default;
// 渲染输出不含 undefined/null km（在 render 已执行的 stub 里检查）
const rendered=(els.carBody?els.carBody.innerHTML:'')+(els.summary?els.summary.innerHTML:'');
ok('渲染输出无undefined', !rendered.includes('undefined'), rendered.slice(0,60));
ok('渲染输出无null km', !rendered.includes('null km'), rendered.slice(0,60));

const bev=EVCARS.filter(c=>c.power==='BEV');
ok('有纯电车型',bev.length>0,bev.length+'款');
let allKwh=true,allRange=true,detail=[];
for(const c of bev){
  const hasKwh=typeof c.kwh==='number'&&c.kwh>0;
  const r=evRangeCalc(c,cond);
  const hasRange=!!r&&typeof r.range==='number'&&r.range>0&&isFinite(r.range);
  if(!hasKwh)allKwh=false;
  if(!hasRange)allRange=false;
  detail.push(`${c.model}:kwh=${c.kwh},range=${r?r.range:null}`);
}
ok('所有纯电车有电量(kwh>0)',allKwh,detail.filter(d=>d.includes('kwh=null')).join(';')||'全部有');
ok('所有纯电有效续航非null且>0',allRange,detail.join('; '));
// 针对性：ID.AURA T6 与 马自达EZ-60 是本次出问题的车
const idt=EVCARS.find(c=>c.model==='ID.AURA T6');
ok('ID.AURA T6 kwh已提取',idt&&idt.kwh>0,idt?('kwh='+idt.kwh):'未找到');
ok('ID.AURA T6 续航非null',idt&&(()=>{const r=evRangeCalc(idt,cond);return r&&r.range>0;})(),idt?('range='+(evRangeCalc(idt,cond)||{}).range):'-');

if(fail.length){console.error('\n失败 '+fail.length+' 项');process.exit(1);}
console.log('\nev_range_test: '+pass+'/'+pass+' 通过（纯电续航无null）');
