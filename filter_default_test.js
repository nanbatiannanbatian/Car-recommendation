// filter_default_test.js — 验证默认过滤不空表 + 条件分组 + 方盒子文案（v9.7.50）
// 由 midsuv_full_regress.py 调用。退出码0=全过。
const fs=require('fs');
const pagePath=process.argv[2];
const html=fs.readFileSync(pagePath,'utf8');
let pass=0, fail=[];
function ok(name,cond,msg){if(cond){pass++;}else{fail.push(name);}console.log((cond?'PASS':'FAIL')+'  '+name+(msg?' :: '+msg:''));}

// 1) 静态：默认过滤值、分组、文案
const pLow=(html.match(/id="pLow" value="([^"]+)"/)||[])[1];
const pHigh=(html.match(/id="pHigh" value="([^"]+)"/)||[])[1];
const bodySel=(html.match(/<select id="bodyFilter">([\s\S]*?)<\/select>/)||[])[1]||'';
ok('默认预算=12-18万', pLow==='12'&&pHigh==='18', pLow+'-'+pHigh);
ok('默认车身=SUV选中', /value="SUV" selected/.test(bodySel));
ok('resetAll默认SUV', /bodyFilter'\)\.value='SUV'/.test(html));
ok('方盒子文案已改', html.includes('方盒子造型')&&!html.includes('方盒子/风味越野造型'));
ok('三组条件渲染', ['核心能源决策','能耗微调','特殊场景'].every(g=>html.includes(g)));
ok('分组JS存在', html.includes('FACTOR_GROUPS')&&html.includes('makeCond'));

// 2) 动态：readCond 能读到每组默认值，默认过滤下有车（非空表）
const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]).join('\n');
const els={};
function mkEl(id){return {id,innerHTML:'',value:'',textContent:'',style:{},children:[],tag:'div',className:'',dataset:{},
  addEventListener(){},appendChild(c){this.children.push(c);c.parent=this;},
  classList:{add(){},remove(){}},querySelectorAll(){return [];},
  querySelector(sel){
    if(sel==='select'&&this.dataset&&this.dataset.fid) return els['c_'+this.dataset.fid]||mkEl('sub');
    return this._sub||(this._sub=mkEl('sub'));
  },
  getAttribute(){return null;},setAttribute(){}};}
global.document={getElementById(id){return els[id]||(els[id]=mkEl(id));},
  createElement(tag){const e=mkEl('new');e.tag=tag;return e;},querySelectorAll(){return [];},querySelector(){return mkEl('q');}};
global.window=global;
els.pLow=mkEl('pLow'); els.pLow.value=pLow;
els.pHigh=mkEl('pHigh'); els.pHigh.value=pHigh;
els.bodyFilter=mkEl('bodyFilter'); els.bodyFilter.value='SUV';
els.wEnergy=mkEl('wEnergy'); els.wEnergy.value='100';
// 预注册 12 个条件下拉，值为 FACTORS 默认（从 HTML FACTORS_JSON 提取，避免硬编码）
const fm=html.match(/const FACTORS=(\{[\s\S]*?\});\s*const FACTOR_GROUPS/);
if(!fm){console.error('FAIL  无法提取FACTORS');process.exit(1);}
const FACTORS=JSON.parse(fm[1]);
for(const [fid,f] of Object.entries(FACTORS)){const e=mkEl('c_'+fid);e.value=f.default;els['c_'+fid]=e;}
try{eval(scripts);}catch(e){console.error('EVAL ERROR',e.message);process.exit(2);}

// 结构验证：conds 直属应是核心8项（.fg），details 容器应收纳其余4项
const conds=els.conds;
const directFg=conds?conds.children.filter(c=>c.className==='fg'):[];
const details=conds?conds.children.filter(c=>c.tag==='details'):[];
ok('核心8项默认平铺', directFg.length===8, '直属fg='+directFg.length);
ok('高级选项details存在', details.length===1, 'details='+details.length);
const advFg=details.length?details[0].children.filter(c=>c.className==='fg'):[];
ok('4项收进折叠区', advFg.length===4, '折叠fg='+advFg.length);
ok('折叠区默认收起', details.length&&!details[0].open);
ok('12项合计无遗漏', directFg.length+advFg.length===12, (directFg.length+advFg.length)+'');

const summary=els.summary?els.summary.innerHTML:'';
const count=els.carCount?els.carCount.textContent:'';
ok('默认状态非空表', !summary.includes('无匹配车型'), summary.replace(/<[^>]+>/g,'').slice(0,80));
ok('默认有匹配车型数>0', /\d+ 款符合条件/.test(count) && !count.includes('0 款'), count);
ok('默认摘要有居首车型', /居首/.test(summary));
ok('默认场景调节生效(非0)', /场景调节 [+-]?[1-9]/.test(summary.replace(/<[^>]+>/g,'')), summary.replace(/<[^>]+>/g,'').slice(0,80));

if(fail.length){console.error('\n失败 '+fail.length+' 项');process.exit(1);}
console.log('\nfilter_default_test: '+pass+'/'+pass+' 通过');
