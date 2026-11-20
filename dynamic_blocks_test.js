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

// 1) 首次加载：定稿区被填充
const fixedThree0=document.getElementById('fixed-three').innerHTML;
const fixedRobust0=document.getElementById('fixed-robust').innerHTML;
ok('钩子onBoardRerender已定义', typeof window.onBoardRerender==='function');
ok('首次加载三榜定稿已算', fixedThree0.length>50 && fixedThree0.includes('纯产品力榜'), fixedThree0.length+'字符');
ok('首次加载稳健性定稿已算', fixedRobust0.length>100 && fixedRobust0.includes('跨度'), fixedRobust0.length+'字符');
ok('首次推演态=默认画像', document.getElementById('sim-state').textContent.includes('默认画像'),
   document.getElementById('sim-state').textContent);
ok('稳健性含6场景', ['无家充','北方极寒','长途高频','营运','限牌城市','默认画像'].every(s=>fixedRobust0.includes(s)));

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

ok('改条件后定稿三榜不变', document.getElementById('fixed-three').innerHTML===fixedThree0);
ok('改条件后定稿稳健性不变', document.getElementById('fixed-robust').innerHTML===fixedRobust0);
const st=document.getElementById('sim-state').textContent;
ok('改条件后推演态标识变化', st.includes('自定义') && st.includes('推演态'), st);

// 3) 恢复默认→标识回到"与定稿一致"，定稿仍不变
window.onBoardRerender(rowsFor(base,1),base,1);
ok('恢复默认后标识回默认', document.getElementById('sim-state').textContent.includes('默认画像'));
ok('恢复默认后定稿三榜仍不变', document.getElementById('fixed-three').innerHTML===fixedThree0);

if(fail.length){console.error('\n失败 '+fail.length+' 项:');fail.forEach(f=>console.error('  '+f));process.exit(1);}
console.log('\ndynamic_blocks_test: '+pass+'/'+pass+' 通过（定稿固定/推演分离成立）');
