// dynamic_blocks_test.js — 验证「定稿榜固定 + 场景推演器分离」（v9.7.49）
// 核心断言：
//  1) 首次加载：定稿区(fixed-three/fixed-robust)被算好；sim-state=默认画像
//  2) 改推演条件：定稿区内容不变；只有 sim-state 变为推演态
// 由 midsuv_full_regress.py 调用。退出码0=全过。
const fs=require('fs');
const pagePath=process.argv[2];
const html=fs.readFileSync(pagePath,'utf8');
const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]).join('\n');

const els={};
function mkEl(id){return {id,innerHTML:'',value:'',textContent:'',style:{},addEventListener(){},appendChild(){},
  classList:{add(){},remove(){}},querySelectorAll(){return [];},querySelector(){return mkEl('sub');},
  getAttribute(){return null;},setAttribute(){}};}
global.document={getElementById(id){return els[id]||(els[id]=mkEl(id));},
  createElement(){return mkEl('dyn');},querySelectorAll(){return [];},querySelector(){return mkEl('q');}};
global.window=global;
['pLow','pHigh','wEnergy'].forEach(id=>els[id]=mkEl(id));
els.pLow.value='0'; els.pHigh.value='999'; els.wEnergy.value='100';
els.bodyFilter=mkEl('bodyFilter'); els.bodyFilter.value='ALL';
// sim-state 在 HTML 里的初始文本（真实页面首次加载钩子尚未定义时的值）——从 HTML 提取，不硬编码
const simInit=(html.match(/id="sim-state"[^>]*>([^<]*)</)||[])[1]||'';
els['sim-state']=mkEl('sim-state'); els['sim-state'].textContent=simInit;

let pass=0, fail=[];
function ok(name,cond,msg){ if(cond){pass++;} else {fail.push(name+(msg?' :: '+msg:''));} console.log((cond?'PASS':'FAIL')+'  '+name+(msg?' :: '+msg:'')); }

const verify=`
;(function(){
  window.__expose={FACTORS:FACTORS,CARS:CARS,adjust:adjust,rangeCalc:rangeCalc,compositeTier:compositeTier};
})();
`;
eval(scripts + verify);
const X=window.__expose;

// 1) 首次加载：定稿区块是服务端渲染的静态 HTML（页面文件里就有，不靠 JS 首算）
const secIds=[...html.matchAll(/id="sec-([a-z_]+)"/g)].map(m=>m[1]);
ok('钩子onBoardRerender已定义', typeof window.onBoardRerender==='function');
ok('定稿区块已服务端渲染(20)', secIds.length===20, secIds.length+'');
ok('定稿含形式适配结论区块', secIds.includes('form_fit') && html.includes('形式均分'));
ok('定稿含画像自检', html.includes('画像自检'));
ok('旧JS首算容器已移除', !html.includes('id="fixed-three"') && !html.includes('id="fixed-robust"'));
ok('首次推演态=默认画像', document.getElementById('sim-state').textContent.includes('默认画像'),
   document.getElementById('sim-state').textContent);
ok('稳健性含全部场景', ['无家充','北方极寒','长途高频','营运','限牌城市','京沪限牌','短持有','增购第二辆'].every(s=>html.includes(s)));

// 2) 模拟用户改推演条件：构造 rows 调 onBoardRerender（推演路径）
const base={}; for(const k of Object.keys(X.FACTORS)) base[k]=X.FACTORS[k].default;
function rowsFor(c,scale){
  return X.CARS.map(car=>{
    const a=X.adjust(car,c);
    const hard=a.hard.reduce((s,h)=>s+(h.pt||0),0);
    const soft=a.total-hard;
    const adj=Math.max(-10,Math.min(10,soft*scale))+hard;
    let tier=X.compositeTier(car.product+adj);
    if(a.hard.some(h=>h.downgrade))tier=tier==='T1'?'T2':(tier==='T2'?'T3':'T3');
    return Object.assign({},car,{price:car.price!=null?car.price:car.priceLow,adjTotal:a.total,hard:a.hard,composite:car.product+adj,tier});
  });
}
const custom={...base,home_charger:'none',plate_limit:'yes',operation:'yes'};
window.onBoardRerender(rowsFor(custom,1.5),custom,1.5);

// 定稿区块是页面静态内容：脚本不得去写 sec-* 容器（否则就是"推演改写了定稿"）
ok('推演不改写定稿区块', !Object.keys(els).some(k=>k.startsWith('sec-')));
const st=document.getElementById('sim-state').textContent;
ok('改条件后推演态标识变化', st.includes('自定义') && st.includes('推演态'), st);

// 3) 恢复默认→标识回到"与定稿一致"
window.onBoardRerender(rowsFor(base,1),base,1);
ok('恢复默认后标识回默认', document.getElementById('sim-state').textContent.includes('默认画像'));
ok('恢复默认后仍不写定稿区块', !Object.keys(els).some(k=>k.startsWith('sec-')));

if(fail.length){console.error('\n失败 '+fail.length+' 项:');fail.forEach(f=>console.error('  '+f));process.exit(1);}
console.log('\ndynamic_blocks_test: '+pass+'/'+pass+' 通过（定稿服务端渲染/推演分离成立）');

