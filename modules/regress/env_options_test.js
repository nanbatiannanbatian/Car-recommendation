// env_options_test.js — 全环境项测试：遍历12项用车条件的每个选项，验证「确实生效 + 结果没问题」
// 由 midsuv_full_regress.py 调用。退出码0=全过。
// 覆盖用户要求：每个环境的修改是不是起效果，结果有没有问题。
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

let pass=0, fail=[];
function ok(name,cond,msg){ if(cond){pass++;} else {fail.push(name+(msg?' :: '+msg:''));} console.log((cond?'PASS':'FAIL')+'  '+name+(msg?' :: '+msg:'')); }

const verify=`
;(function(){
  window.__F=FACTORS; window.__CARS=CARS; window.__adjust=adjust; window.__rangeCalc=rangeCalc;
  window.__compositeTier=compositeTier;
})();
`;
eval(scripts + verify);
const ENV_FACTORS=window.__F, ENV_CARS=window.__CARS, ENV_adjust=window.__adjust, ENV_rangeCalc=window.__rangeCalc;

const base={};
for(const k of Object.keys(ENV_FACTORS)) base[k]=ENV_FACTORS[k].default;
// 设计为"中性/不调节"的选项（hint 明确写"不调节"/"默认"/"接近平衡点"）：
// 选中它们=回到基准，本就不应改变结果。白名单依据 FACTORS 的 hint 文案。
const NEUTRAL_OPTIONS=new Set(['annual_km=10to15','hold_years=3to5']);
// 防滥用：白名单每项的实际 hint 必须含中性关键词，否则视为真 bug 用白名单掩盖
function hintOf(fid,v){const f=ENV_FACTORS[fid];const o=f.options.find(x=>x.v===v);return o?(o.hint||''):'';}
for(const key of NEUTRAL_OPTIONS){
  const [fid,v]=key.split('=');
  const h=hintOf(fid,v);
  ok(`中性白名单有据:${key}`, /不调节|默认|平衡点|无长途|按实际/.test(h), 'hint='+h);
}

function snapshot(cond){
  return ENV_CARS.map(c=>{
    const a=ENV_adjust(c,cond);
    const rc=ENV_rangeCalc(c,cond);
    const hard=a.hard.reduce((s,h)=>s+(h.pt||0),0);
    const soft=a.total-hard;
    return {m:c.model,p:c.power,soft,hard,total:a.total,eff:rc?rc.range:null,cons:rc?rc.sceneCons:null,
            hardLabels:a.hard.map(h=>h.label)};
  });
}
const s0=snapshot(base);

// 1) 12项全部存在且结构完整
ok('用车条件项数=12', Object.keys(ENV_FACTORS).length===12, Object.keys(ENV_FACTORS).length+'');
for(const [fid,f] of Object.entries(ENV_FACTORS)){
  ok('条件结构完整:'+fid, Array.isArray(f.options)&&f.options.length>=2&&f.default&&f.options.some(o=>o.v===f.default));
}

// 2) 每个非默认选项都必须「起效果」：软分/硬条件/续航/电耗 至少之一变化
console.log('\n=== 每个选项影响力（变化车数: 软分/硬条件/续航）===');
const noEffect=[];
for(const [fid,f] of Object.entries(ENV_FACTORS)){
  for(const opt of f.options){
    if(opt.v===f.default) continue;  // 默认选项与基准相同，跳过
    const s=snapshot({...base,[fid]:opt.v});
    let cSoft=0,cHard=0,cEff=0,nan=0,badTier=0;
    for(let i=0;i<s0.length;i++){
      const A=s0[i],B=s[i];
      if(A.soft!==B.soft)cSoft++;
      if(A.hard!==B.hard||A.hardLabels.join()!==B.hardLabels.join())cHard++;
      if(A.eff!==B.eff)cEff++;
      if(!isFinite(B.soft)||!isFinite(B.hard)||!isFinite(B.total))nan++;
      if((B.eff!==null)&&(!isFinite(B.eff)||B.eff<0||B.eff>2000))nan++;
    }
    const key=fid+'='+opt.v;
    const isNeutral=NEUTRAL_OPTIONS.has(key);
    const effective=(cSoft+cHard+cEff)>0;
    console.log(`  ${fid.padEnd(14)} ${String(opt.v).padEnd(10)} 软${String(cSoft).padStart(2)} 硬${String(cHard).padStart(2)} 续航${String(cEff).padStart(2)} ${isNeutral?'(设计为中性)':(effective?'':'⚠无效')}`);
    if(isNeutral){
      ok(`中性选项无影响:${key}`, !effective, `软${cSoft}/硬${cHard}/续航${cEff}`);
    }else{
      ok(`生效:${key}`, effective, `软${cSoft}/硬${cHard}/续航${cEff}`);
    }
    ok(`无NaN/越界:${key}`, nan===0, 'nan='+nan);
    if(!effective && !isNeutral) noEffect.push(key);
  }
}

// 3) 关键硬条件阈值正确（结果正确性）
console.log('\n=== 关键硬条件/调节阈值 ===');
function findHard(cond,label){return snapshot(cond).filter(x=>x.hardLabels.some(l=>l.includes(label)));}
const noCharger=findHard({...base,home_charger:'none'},'无充电桩');
ok('无家充→纯电触发-15硬条件', noCharger.length>0&&noCharger.every(x=>x.p==='BEV')&&
   noCharger.every(x=>x.hard===-15), noCharger.length+'款');
const plate=findHard({...base,plate_limit:'yes'},'限牌');
ok('限牌→燃油/油混触发-8硬条件', plate.length>0&&plate.every(x=>x.p==='ICE'||x.p==='HEV')&&
   plate.every(x=>x.hard===-8), plate.length+'款');
const op=findHard({...base,operation:'yes'},'营运');
ok('营运→燃油/油混触发-10硬条件', op.length>0&&op.every(x=>x.p==='ICE'||x.p==='HEV')&&
   op.every(x=>x.hard===-10), op.length+'款');
const cold=findHard({...base,winter_temp:'underm20'},'极寒');
ok('极寒→低价纯电触发-12硬条件', cold.length>0&&cold.every(x=>x.p==='BEV'&&x.hard===-12), cold.length+'款');
// over8 修复：纯电+1.0（hint承诺"纯电加分"，此前引擎漏实现）
const sOver8=snapshot({...base,hold_years:'over8'});
const bevOver8=sOver8.filter(x=>x.p==='BEV');
ok('over8→纯电软分+1.0(修复项)', bevOver8.length>0&&bevOver8.every(x=>Math.abs(x.soft-s0.find(a=>a.m===x.m).soft-1.0)<1e-6),
   bevOver8.length+'款纯电');

// 4) 极端组合下所有结果仍合法（无NaN、分数有限）
console.log('\n=== 极端组合稳健性 ===');
const combos=[
  {...base,home_charger:'none',winter_temp:'underm20',hold_years:'under3',long_trip_freq:'weekly',single_trip:'over500'},
  {...base,operation:'yes',plate_limit:'yes',highway_ratio:'over60',boxy:'yes',drive_style:'aggressive',ac_usage:'heavy'},
  {...base,annual_km:'under10',long_trip_freq:'rare',hold_years:'over8',home_charger:'home'},
];
combos.forEach((c,i)=>{
  const s=snapshot(c);
  ok('极端组合'+i+'结果合法', s.every(x=>isFinite(x.soft)&&isFinite(x.hard)&&isFinite(x.total)&&
     (x.eff===null||(isFinite(x.eff)&&x.eff>=0))));
});

if(fail.length){console.error('\n失败 '+fail.length+' 项:');fail.forEach(f=>console.error('  '+f));process.exit(1);}
console.log('\nenv_options_test: '+pass+'/'+pass+' 通过（12项×每选项全生效，结果无异常）');
