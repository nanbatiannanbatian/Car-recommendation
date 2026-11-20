// dynamic_blocks_test.js — 用极简 DOM stub 跑页面JS，验证4个动态区块随环境分实时重绘
// 由 midsuv_full_regress.py 调用。退出码0=全过。
const fs=require('fs');
const pagePath=process.argv[2];
const html=fs.readFileSync(pagePath,'utf8');
const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]).join('\n');

const els={};
function mkEl(id){return {id,innerHTML:'',value:'',style:{},addEventListener(){},appendChild(){},
  classList:{add(){},remove(){}},querySelectorAll(){return [];},querySelector(){return mkEl('sub');},
  getAttribute(){return null;},setAttribute(){}};}
global.document={getElementById(id){return els[id]||(els[id]=mkEl(id));},
  createElement(){return mkEl('dyn');},querySelectorAll(){return [];},querySelector(){return mkEl('q');}};
global.window=global;
['pLow','pHigh','wEnergy'].forEach(id=>els[id]=mkEl(id));
els.pLow.value='0'; els.pHigh.value='999'; els.wEnergy.value='100';
els.bodyFilter=mkEl('bodyFilter'); els.bodyFilter.value='ALL';

let failed=[];
function ok(name,cond){ console.log((cond?'PASS':'FAIL')+'  '+name); if(!cond)failed.push(name); }

const verify=`
;(function(){
  var ids=['dyn-tiers','dyn-bands','dyn-three','dyn-robust'];
  function snap(){return ids.map(function(id){return document.getElementById(id).innerHTML;});}
  var s0=snap();
  window.__firstFilled=s0.every(function(x){return x.length>20;});
  var cond={operation:'no',winter_temp:'m10to0',annual_km:'15to25',single_trip:'300to500',long_trip_freq:'few',home_charger:'home',plate_limit:'no',hold_years:'5to8',highway_ratio:'20to40',boxy:'no',drive_style:'normal',ac_usage:'normal'};
  function rowsFor(c){return CARS.map(function(car){var a=adjust(car,c);var hard=a.hard.reduce(function(s,h){return s+(h.pt||0);},0);var soft=a.total-hard;var adj=Math.max(-10,Math.min(10,soft*1))+hard;var tier=compositeTier(car.product+adj);if(a.hard.some(function(h){return h.downgrade;}))tier=tier==='T1'?'T2':(tier==='T2'?'T3':'T3');return Object.assign({},car,{price:car.price!=null?car.price:car.priceLow,adjTotal:a.total,hard:a.hard,composite:car.product+adj,tier:tier});});}
  window.onBoardRerender(rowsFor(cond),cond,1);
  var sA=snap();
  var condB={}; for(var k in cond)condB[k]=cond[k]; condB.home_charger='none';condB.plate_limit='yes';condB.operation='yes';
  window.onBoardRerender(rowsFor(condB),condB,1);
  var sB=snap();
  window.__changed=sA.map(function(x,i){return x!==sB[i];});
  window.__t1count=[(sA[0].match(/T1（\\d+款）/g)||[])[0],(sB[0].match(/T1（\\d+款）/g)||[])[0]];
})();
`;
eval(scripts + verify);
ok('onBoardRerender钩子已定义', typeof window.onBoardRerender==='function');
ok('首次加载4区块均填充', window.__firstFilled===true);
const names=['三档分级','价位子榜','三榜并列','稳健性'];
window.__changed.forEach((v,i)=>ok('动态区块随条件变化:'+names[i], v===true));
console.log('T1数量 默认→极端:', JSON.stringify(window.__t1count));
if(failed.length){console.error('失败 '+failed.length+' 项');process.exit(1);}
console.log('dynamic_blocks_test: 5/5 通过');
