/* ============================================================================
 * engine.js —— 跨能源「环境分 + 实际续航 + 持有成本」引擎（浏览器交互镜像）
 *
 * v10.52.0 架构变更（2026-11-24 跨能源比较优化）：
 *   规则单一事实源 = modules/envscore/engine.py（Python，服务端定稿渲染 + 可回归）；
 *   本文件是浏览器交互镜像（场景推演器实时重算必须跑在页面里）。
 *   两套实现由 modules/regress/env_equivalence_regress.py 守护：同一批 fixtures ×
 *   画像，node 实跑本文件 vs Python engine，调整分/硬条件/续航/TCO 必须一致——
 *   防两套口径漂移（项目"禁止两处系数"纪律）。
 *
 * 这是 modules/envscore 的交互侧核心：给一台车 + 一套用车条件（15 项），算出
 *   1) rangeCalc / effRange：真实续航（PHYS 物理常数，服务端与浏览器同源）
 *   2) adjust：环境分（15 项用车条件的软加减分 + 硬闸门 hard 标记 + 逐条件判定）
 *   3) tco：8 年持有成本测算（仅披露不入分）
 *   4) compositeTier：按综合分划 T1/T2/T3
 *
 * 依赖注入（构建时由 build_energy_compare.py 写入，不要在本文件里写死）：
 *   PHYS  —— 物理常数 + TCO 常量对象，来自 modules/physics/ev_range_model.py 与 envscore/engine.py
 *   （车列表 CARS、条件表 FACTORS 由页面注入，本文件只消费）
 *
 * 纪律：本文件不放任何 DOM 操作。页面渲染（render/resetAll）留在
 * build_energy_compare.py 的模板里；node 回归只跑本文件的纯函数。
 * ========================================================================== */

// ===== 物理续航模型（与 engine.py 同源）=====
const DEMAND={under150:100,'150to300':220,'300to500':400,over500:600};
function compositeTier(score){ return score>=82?'T1':(score>=72?'T2':'T3'); }

function hwyMult(car,cond){
  let r=car.cdaPerTon;
  if(r==null) r=PHYS.medRatio;
  let m=PHYS.hwyBase*(1+0.45*(r/PHYS.medRatio-1));
  return Math.max(1.35,Math.min(2.35,m));
}

function rangeCalc(car,cond){
  if(car.power!=='BEV') return null;
  const hwWeight={under20:0.0,'20to40':0.25,'40to60':0.7,over60:1.0}[cond.highway_ratio];
  const hwM=hwyMult(car,cond);
  const sceneM=PHYS.cityBase*(1-hwWeight)+hwM*hwWeight;
  const tempM=PHYS.temp[cond.winter_temp];
  const acM=(PHYS.acSeason[cond.winter_temp]||1.0);   // 冬季暖风基线（空调强度条件已移除）
  let voltM=1.0;
  if(car.voltage){
    const vs=Object.keys(PHYS.volt).map(Number).sort((a,b)=>a-b);
    let best=vs[0]; for(const v of vs) if(Math.abs(v-car.voltage)<Math.abs(best-car.voltage)) best=v;
    voltM=PHYS.volt[best];
  }
  // 地形条件已移除（v10.56.0）：场景电耗不再乘地形系数
  const sceneCons=car.cons*sceneM*tempM*acM*voltM;
  if(!car.kwh){
    return {range:null, method:'无电量数据', cls:car.body,
      steps:['该车型无电池电量数据，无法估算续航（不编造）']};
  }
  const rng=Math.round(car.kwh/sceneCons*100);
  const steps=[
    '基准电耗 '+car.cons+' kWh/100km（'+car.consSrc+'）',
  ];
  if(car.consSrc==='物理估算'){
    steps[0]='物理回归估算电耗 '+car.cons+'（4.48×车重t + 5.88×CdA + 0.61）';
    steps.push('  车重 '+(car.weight||'—')+'kg · Cd '+(car.cd||'—')+' · 迎风面积 '+(car.area||'—')+'m²');
  }
  steps.push('× 工况倍率 '+sceneM.toFixed(2)+(hwWeight>=0.5?'（偏高速，CdA/吨 '+(car.cdaPerTon||'—')+'）':'（偏市区）'));
  steps.push('× 温度(仅电池) '+tempM+' × 暖风 '+acM.toFixed(2)+' × 电压系数 '+voltM+(car.voltage?'（'+car.voltage+'V平台）':''));
  steps.push('= 场景电耗 '+sceneCons.toFixed(1)+' kWh/100km');
  steps.push('电量 '+car.kwh+' kWh ÷ 场景电耗 ×100 = 有效续航 '+rng+' km');
  return {range:rng, method:car.consSrc==='事实库'?'物理模型(真实电耗基准)':'物理模型(估算电耗基准)',
          cls:car.body, sceneCons:Math.round(sceneCons*10)/10, steps};
}
function effRange(car,cond){ const r=rangeCalc(car,cond); return r?r.range:null; }

// ===== 持有成本测算 TCO（与 engine.py 同源；仅披露不入分）=====
function homeElecPrice(cond){
  const T=PHYS.tco;
  return T.elecHome;   // 固定谷电口径（电价档条件已移除）
}
function unitEnergyCost(car,cond){
  const T=PHYS.tco;
  const charger=cond.home_charger;
  const homePrice=homeElecPrice(cond);
  const P=car.power;
  const cons=car.cons;
  if(P==='BEV'){
    const per100=cons||T.bevKwhPer100km;
    const home=per100*homePrice/100, pub=per100*T.elecPublic/100;
    const work=per100*(0.8*T.elecWork+0.2*T.elecPublic)/100;
    if(charger==='work') return +work.toFixed(3);
    return charger==='home'?home:(charger==='public'?+(home*0.3+pub*0.7).toFixed(3):pub);
  }
  function fuelL(){
    if(car.fuelDeprivedL100) return car.fuelDeprivedL100;
    if(car.fuelL100) return car.fuelL100;
    return ({PHEV:T.phevFuelL,EREV:T.erevFuelL,HEV:T.hevFuelL})[P]||T.iceFuelL;
  }
  const evPrice=charger==='work'?T.elecWork:homePrice;
  const per100=cons||T.bevKwhPer100km;
  if(P==='PHEV'){
    const evShare=charger==='home'?0.8:(charger==='work'?0.7:(charger==='public'?0.5:0.3));
    return +(evShare*evPrice/100*per100+(1-evShare)*fuelL()*T.oilPrice/100).toFixed(3);
  }
  if(P==='EREV'){
    const evShare=charger==='home'?0.7:(charger==='work'?0.6:(charger==='public'?0.5:0.3));
    return +(evShare*evPrice/100*per100+(1-evShare)*fuelL()*T.oilPrice/100).toFixed(3);
  }
  return +(fuelL()*T.oilPrice/100).toFixed(3);
}
function idleCost(car,cond){
  return 0;   // 驻车用电条件已移除（保留函数为兼容旧调用）
}
function tco(car,cond){
  const T=PHYS.tco;
  const P=car.power;
  // 车价链路单位是万元（H2「≤15万」/页面报价都按万元），进 TCO 先换成元（v10.59.0 修正单位错配）
  const price=(car.price||car.priceLow||0)*10000;
  const km=PHYS.annualKmMid[cond.annual_km];
  const unit=unitEnergyCost(car,cond);
  const energy8=unit*km*T.years;
  const ins8=(P==='BEV'||P==='PHEV'||P==='EREV'?T.insNev:T.insIce)*T.years;
  const maint8=(T.maint[P]||T.maint.ICE)*T.years;
  const isNev=(P==='BEV'||P==='PHEV'||P==='EREV');
  let tax=price/1.13*(isNev?T.taxNevRate:T.taxIceRate);
  if(isNev&&T.taxNevCap!=null) tax=Math.min(tax,T.taxNevCap);
  const residualLoss=price*(1-T.residual3[P]);
  const batteryRisk8=(T.batteryRisk[P]||0)*T.years;
  const idle8=idleCost(car,cond);
  // v10.59.0：轮胎按里程折损 + 非事故易损/电子件年预算（与 engine.py 同口径）
  const tireLife=(T.tireLifeKm&&T.tireLifeKm[P])||60000;
  const tirePrice=(T.tirePrice4&&T.tirePrice4[P])||2400;
  const tireUnit=tirePrice/tireLife;
  const tire8=tireUnit*km*T.years;
  const wear8=((T.wear&&T.wear[P])||0)*T.years;
  const total=energy8+ins8+maint8+tax+residualLoss+batteryRisk8+idle8+tire8+wear8;
  return {power:P,price:price,annualKm:km,unitEnergy:+unit.toFixed(3),unitTire:+tireUnit.toFixed(4),
    energy8y:Math.round(energy8),ins8y:Math.round(ins8),maint8y:Math.round(maint8),
    tire8y:Math.round(tire8),wear8y:Math.round(wear8),
    purchaseTax:Math.round(tax),residualLoss:Math.round(residualLoss),
    batteryRisk8y:Math.round(batteryRisk8),idle8y:idle8,elecPrice:homeElecPrice(cond),
    tco8y:Math.round(total),
    variablePerKm:+(unit+tireUnit).toFixed(3),
    fixed8y:Math.round(total-energy8-tire8),
    tcoPerKm:+(total/(km*T.years)).toFixed(3),basis:PHYS.tcoBasis};
}
function breakevenKm(cars,cond){
  const bev=cars.filter(c=>c.power==='BEV');
  const ice=cars.filter(c=>c.power==='ICE'||c.power==='HEV');
  if(!bev.length||!ice.length) return null;
  const b=tco(bev[0],Object.assign({},cond,{annual_km:'15to25',home_charger:'home'}));
  let bestIce=null;
  for(const c of ice){ const t=tco(c,cond); if(!bestIce||t.tco8y<bestIce.tco8y) bestIce=t; }
  const fixedB=b.fixed8y;
  const fixedI=bestIce.fixed8y;
  const unitDiff=bestIce.variablePerKm-b.variablePerKm;
  if(unitDiff<=0) return null;
  return Math.round((fixedB-fixedI)/(PHYS.tco.years*unitDiff));
}
function breakevenKmRange(cars,cond,spread){
  const sp=(spread==null?0.3:spread);
  const mid=breakevenKm(cars,cond);
  if(mid==null) return null;
  // 固定项整体缩放做区间（非能源成本是估算，点值会假精确）
  const bev=cars.filter(c=>c.power==='BEV');
  const ice=cars.filter(c=>c.power==='ICE'||c.power==='HEV');
  if(!bev.length||!ice.length) return null;
  const b=tco(bev[0],Object.assign({},cond,{annual_km:'15to25',home_charger:'home'}));
  let bestIce=null;
  for(const c of ice){ const t=tco(c,cond); if(!bestIce||t.tco8y<bestIce.tco8y) bestIce=t; }
  const unitDiff=bestIce.variablePerKm-b.variablePerKm;
  if(unitDiff<=0) return {km:mid,low:null,high:null,spread:sp};
  const at=s=>Math.round((b.fixed8y*s-bestIce.fixed8y*s)/(PHYS.tco.years*unitDiff));
  const lo=at(1-sp), hi=at(1+sp);
  return {km:mid,low:Math.min(lo,hi),high:Math.max(lo,hi),spread:sp};
}

// ===== 场景调节（与 engine.py adjust 同源）=====
function adjust(car,cond){
  const items=[]; let total=0;
  const hard=[];
  const add=(label,pt)=>{ if(pt){items.push({label,pt}); total+=pt;} };
  const addHard=(label,pt,reason,downgrade)=>{ if(pt){hard.push({label,pt,reason,downgrade:!!downgrade}); total+=pt;} };
  const P=car.power;
  const isSwap=car.swap===true;
  const price=car.price;

  // ===== 硬条件（大扣分 + 降档 + 标注）=====
  // H1 无固定充电 + 纯电（非换电）
  if(cond.home_charger==='none' && P==='BEV'){
    if(isSwap){
      addHard('无家充·换电缓解', PHYS.swapAdv.no_charger.gain, PHYS.swapAdv.no_charger.reason, false);
    }else{
      addHard('无充电桩·纯电不推荐', -15, '无固定充电，纯电日常补能困难、体验大幅打折；换电车型不受此限', true);
    }
  }
  // H2 极寒 + 低价纯电（≤15万）
  if(cond.winter_temp==='underm20' && P==='BEV'){
    if(isSwap){
      addHard('极寒·换电缓解', PHYS.swapAdv.cold.gain, PHYS.swapAdv.cold.reason, false);
    }else if(price!=null && price<=15){
      addHard('极寒+低价纯电·谨慎', -12, '-20℃以下低价纯电续航常腰斩、充电变慢，北方选购需谨慎；换电或大电池车型更稳', true);
    }else{
      addHard('极寒·换电优势', PHYS.swapAdv.cold.gain, PHYS.swapAdv.cold.reason, false);
    }
  }
  // H3 短持有(2-3年) + 纯电
  if(cond.hold_years==='under3' && P==='BEV'){
    if(isSwap){
      addHard('短持有·换电合适', PHYS.swapAdv.short_hold.gain, PHYS.swapAdv.short_hold.reason, false);
    }else{
      addHard('短持有·纯电折旧快', -8, '2-3年换车，纯电折旧快、残值损失大；换电车型车电分离更合适', true);
    }
  }
  // H4 长途高频 + 纯电续航明显不足
  if((cond.long_trip_freq==='weekly'||cond.long_trip_freq==='monthly') && P==='BEV'){
    const eff=effRange(car,cond), d=DEMAND[cond.single_trip];
    if(eff!=null && eff < d*0.75){
      if(isSwap) addHard('长途+换电缓解', PHYS.swapAdv.longtrip.gain, PHYS.swapAdv.longtrip.reason, false);
      else addHard('长途续航不足·不推荐', -10, '长途频繁且单次里程远超有效续航，纯电补能时间成本高', true);
    }
  }
  // H5 限牌 + 燃油/油混
  if((cond.plate_limit==='yes'||cond.plate_limit==='beijing'||cond.plate_limit==='shanghai') && (P==='ICE'||P==='HEV')){
    addHard('限牌城市·上牌难', -8, '限牌城市燃油/油混需摇号拍牌，新能源可直接上牌', true);
  }
  // H6 营运 + 燃油/油混
  if(cond.operation==='yes' && (P==='ICE'||P==='HEV')){
    addHard('营运·能耗成本高', -10, '营运高频，燃油/油混能耗成本远高于纯电', true);
  }
  // H7（v10.52.0）限牌京沪 + 插混/增程：北京插混增程按油车管理、上海插混无免费绿牌
  if((cond.plate_limit==='beijing'||cond.plate_limit==='shanghai') && (P==='PHEV'||P==='EREV')){
    const city=cond.plate_limit==='beijing'?'北京':'上海';
    addHard('限牌'+city+'·插混增程无绿牌优势', -8, city+'插混/增程不享新能源绿牌（北京按油车管理需燃油指标/上海无免费绿牌），上牌成本与燃油车同', true);
  }

  // ===== 常规软调节 =====
  const eff=effRange(car,cond), d=DEMAND[cond.single_trip];
  if(P==='BEV'){
    if(eff!=null){
      const gap=(d-eff)/d;
      let pt = gap<=0.10?0 : gap<=0.25?-2 : gap<=0.40?-3 : gap<=0.60?-4.5 : -6;
      if(eff>=d*1.3) pt=1;
      add('续航匹配(需'+d+'/有效'+eff+'km)',pt);
    }else{
      items.push({label:'续航匹配：无续航数据，不参与',pt:0,na:true});
    }
  }
  if(cond.long_trip_freq==='monthly'||cond.long_trip_freq==='weekly'){
    add('长途频率高', P==='BEV'?-1.5 : ((P==='ICE'||P==='HEV')?1.5 : 0));
  }else if(cond.long_trip_freq==='rare'){ add('几乎无长途', P==='BEV'?1:0); }
  if(cond.home_charger==='home') add('有家充', P==='BEV'?1.5 : ((P==='PHEV'||P==='EREV')?1:0));
  else if(cond.home_charger==='work') add('单位可充电', P==='BEV'?1.0 : ((P==='PHEV'||P==='EREV')?0.8:0));
  if(cond.home_charger==='none' && P!=='BEV') add('无固定充电', (P==='PHEV'||P==='EREV')?-1.5 : 1);
  if(cond.operation==='yes') add('营运', P==='BEV'?2:0);
  if(cond.highway_ratio==='over60') add('常年高速', P==='EREV'?-2 : (P==='BEV'?-1 : 1));
  else if(cond.highway_ratio==='40to60') add('高速较多', P==='EREV'?-1:0);
  else if(cond.highway_ratio==='under20') add('市区为主', P==='BEV'?1:0);
  if(cond.annual_km==='over25') add('高里程', P==='BEV'?1.5 : (P==='ICE'?-1:0));
  else if(cond.annual_km==='under5') add('超低里程', P==='BEV'?-2.0 : ((P==='ICE'||P==='HEV')?2.0:0));
  else if(cond.annual_km==='under10') add('低里程', P==='BEV'?-1 : ((P==='ICE'||P==='HEV')?1:0));
  if(cond.hold_years==='over8') add('长期持有·电车收益累积', P==='BEV'?1.0 : ((P==='PHEV'||P==='EREV')?0.3 : ((P==='ICE'||P==='HEV')?-0.3:0)));
  // v10.52.0 新增软调节（与 engine.py 逐一对应）
  if(P==='BEV' && hard.length===0) add('纯电优先（无硬条件冲突）', 0.5);
  if(cond.charge_patience==='reluctant'){
    const longNeed=cond.long_trip_freq==='monthly'||cond.long_trip_freq==='weekly'||cond.single_trip==='over500';
    if(P==='BEV') add('补能耐心不足+长途需求', longNeed?-2:-1);
    else add('补能时间不敏感', 0.5);
  }
  if((cond.annual_km==='under5'||cond.annual_km==='under10') && P==='BEV' && cond.home_charger==='none') add('低里程无桩纯电·固定成本摊薄慢', cond.annual_km==='under5'?-1.5:-1);
  // ===== v10.53.0 新增软调节（与 engine.py 逐一对应）=====
  if(cond.fleet==='second'){
    add('家庭增购·已有油车兜底', P==='BEV'?2.0 : (P==='PHEV'?0.6 : (P==='EREV'?0.4 : (P==='ICE'?-1.0:0))));
  }
  // 地形/方盒子条件已移除（v10.56.0），对应规则同步删除
  // 停放环境/驻车用电/露营/电价档条件已移除（v10.54.0），对应规则同步删除

  return {items:items, total:+total.toFixed(2), hard:hard, verdicts:verdicts(car,cond,eff,d)};
}

// ===== 逐条件适配判定（与 engine.py verdicts 同源）=====
function verdicts(car,cond,eff,d){
  const P=car.power, price=car.price;
  const v={};
  if(cond.operation==='yes'){
    if(P==='BEV'||P==='PHEV'||P==='EREV') v.operation=['适配','营运高频：纯电/插混/增程能耗成本碾压燃油（网约车新能源渗透率76%）'];
    else v.operation=['不建议','营运高频：燃油/油混能耗成本远高于纯电（硬条件H6 −10）'];
  }
  const wt=cond.winter_temp;
  if(wt==='underm20'){
    if(P==='BEV'){
      if(price!=null && price<=15) v.winter_temp=['不建议','极寒+低价纯电：续航常腰斩、充电慢（硬条件H2 −12）'];
      else v.winter_temp=['谨慎','极寒纯电续航大幅打折，优先换电/大电池/热泵车型'];
    }else if(P==='PHEV'||P==='EREV') v.winter_temp=['适配','发动机余热兜底+加油补能；冬季纯电续航按×0.5~0.7规划'];
    else v.winter_temp=['适配','燃油/油混低温稳定，油耗升高约20-30%'];
  }else if(wt==='m20tom10'){
    v.winter_temp=(P==='BEV')?['谨慎','纯电：-10℃续航达成约68%（硬条件不触发但需规划）']:['适配','有油箱/发动机兜底，影响小'];
  }
  const ak=cond.annual_km;
  if(ak==='under5'){
    if(P==='BEV') v.annual_km=['不建议','超低里程（<5000km/年）：远低于盈亏平衡点（约1.4万km/年），纯电省回的能源差额抵不过保险溢价与残值差'];
    else v.annual_km=['适配','超低里程：能源成本占比极小，油车/油混总花费更低'];
  }else if(ak==='under10'){
    if(P==='BEV') v.annual_km=[(cond.home_charger!=='none'?'谨慎':'不建议'),'低里程：纯电能源节省无法覆盖保险溢价与残值差（TCO 测算见持有成本区块）'];
    else v.annual_km=['适配','低里程油车/油混省心，能源成本占比低'];
  }else if(ak==='over25'){
    v.annual_km=(P!=='ICE')?['适配','高里程：纯电/混动能源成本优势累积']:['谨慎','高里程油费占比高，慎选纯燃油'];
  }
  if(eff!=null && P==='BEV'){
    if((cond.long_trip_freq==='monthly'||cond.long_trip_freq==='weekly') && eff<d*0.75) v.single_trip=['不建议','长途高频且有效续航'+eff+'km < 需求'+d+'km×0.75（硬条件H4 −10）'];
    else if(eff<d) v.single_trip=['谨慎','有效续航'+eff+'km 低于单次需求'+d+'km，需途中补能'];
    else v.single_trip=['适配','有效续航'+eff+'km 覆盖单次需求'+d+'km'];
  }else if(P==='BEV'){
    v.single_trip=['谨慎','无续航数据不估算，长途需求请人工核实'];
  }else{
    const note=(P==='EREV'&&(cond.highway_ratio==='over60'||cond.highway_ratio==='40to60'))?'；增程高速亏电油耗7-9L高于插混直驱5-6L':'油箱兜底';
    v.single_trip=['适配','非纯电'+note];
  }
  const hc=cond.home_charger;
  if(hc==='none'){
    if(P==='BEV') v.home_charger=['不建议','无固定充电，纯电日常补能困难（硬条件H1 −15）'];
    else if(P==='PHEV'||P==='EREV') v.home_charger=['谨慎','无家充插混/增程长期亏电运行，油耗经济性恶化'];
    else v.home_charger=['适配','加油即走，无充电依赖'];
  }else if(hc==='public'){
    v.home_charger=(P==='BEV')?['谨慎','纯电依赖公充：成本约为家充3-5倍、高峰排队']:['适配','不依赖家充'];
  }else if(hc==='work'){
    if(P==='BEV'||P==='PHEV'||P==='EREV') v.home_charger=['适配','单位/公司可充电：白天补能、成本接近家充，不按「无固定充电」处罚'];
    else v.home_charger=['适配','加油即走，不依赖充电'];
  }
  const pl=cond.plate_limit;
  if(pl==='beijing'||pl==='shanghai'){
    const city=pl==='beijing'?'北京':'上海';
    if(P==='BEV') v.plate_limit=['适配',city+'纯电享免费绿牌'];
    else if(P==='PHEV'||P==='EREV') v.plate_limit=['不建议',city+'插混/增程不享绿牌（北京按油车管理/上海无免费绿牌）（硬条件H7 −8）'];
    else v.plate_limit=['不建议',city+'燃油/油混需摇号或拍牌（硬条件H5 −8）'];
  }else if(pl==='yes'){
    if(P==='ICE'||P==='HEV') v.plate_limit=['不建议','限牌城市燃油/油混需摇号拍牌（硬条件H5 −8）'];
    else v.plate_limit=['适配','新能源直接上牌'];
  }
  const hy=cond.hold_years;
  if(hy==='under3'){
    if(P==='BEV') v.hold_years=['不建议','短持有纯电折旧快（硬条件H3 −8）；换电车型车电分离除外'];
    else if(P==='PHEV'||P==='EREV') v.hold_years=['谨慎','短持有插混/增程残值中等受压'];
    else v.hold_years=['适配','短持有燃油/油混残值相对稳'];
  }else if(hy==='over8' && P==='BEV'){
    v.hold_years=['适配','长期持有：能源节省累积、残值影响摊薄'];
  }
  const hr=cond.highway_ratio;
  if(hr==='over60'){
    if(P==='EREV') v.highway_ratio=['谨慎','常年高速：增程亏电油耗7-9L（油→电→轮二次转换），高于插混直驱5-6L'];
    else if(P==='BEV') v.highway_ratio=['谨慎','常年高速：纯电高速续航达成56-75%，补能时间成本高'];
    else if(P==='PHEV') v.highway_ratio=['适配','插混高速发动机直驱效率高（亏电3.3-5.5L）'];
    else v.highway_ratio=['适配','燃油/油混高速工况稳定'];
  }
  // 方盒子条件已移除（v10.56.0）：车身形状由车型风阻承担，判定同步删除
  if((cond.charge_patience||'tolerate')==='reluctant' && P==='BEV'){
    const longNeed=cond.long_trip_freq==='monthly'||cond.long_trip_freq==='weekly'||cond.single_trip==='over500';
    v.charge_patience=[longNeed?'不建议':'谨慎','补能时间敏感+纯电途中充电等待（800V约10-20分钟/400V约30-40分钟，节假日排队另计）'];
  }
  // 露营/停放/驻车/电价档条件已移除（v10.54.0），对应判定同步删除
  // ===== v10.53.0 新增判定（与 engine.py 同源）=====
  if(cond.fleet==='second'){
    if(P==='BEV') v.fleet=['适配','增购第二辆：长途/返乡由已有油车承担，纯电只管日常，分工最省钱'];
    else if(P==='ICE') v.fleet=['谨慎','第二辆车再买燃油车：日常能源成本无优势，与已有油车功能重叠'];
    else if(P==='PHEV'||P==='EREV') v.fleet=['适配','第二辆可油可电：与油车功能部分重叠，胜在无补能焦虑'];
  }
  // 地形条件已移除（v10.56.0）：路线级平均不够准，改为方法区提示自行上浮电耗
  return v;
}

// ===== 能源形式聚合结论 + 画像自检（v10.53.0 / 形式合并 v10.57.0，与 engine.py 同源）=====
const FORM_CN={ICE:'燃油',HEV:'油混',PHEV:'插混',EREV:'增程',BEV:'纯电'};
// v10.57.0：形式结论按 3 个家族（燃油含油混 / 插混·增程 / 纯电）；逐车标签仍保留油混 vs 燃油
const FORM_FAMILY={ICE:'FUEL',HEV:'FUEL',PHEV:'NEV',EREV:'NEV',BEV:'BEV'};
const FAMILY_CN={FUEL:'燃油（含油混）',NEV:'插混·增程',BEV:'纯电'};
const FORM_MIN_N=3;   // 形式均分最小样本量：<3 款标「样本不足」，不参与形式推荐
function formName(code){ return FAMILY_CN[code]||FORM_CN[code]||code; }
function recommendForm(rows,cond){
  const agg={};
  for(const r of rows){
    const p=r.power, fam=FORM_FAMILY[p]||p;
    if(!agg[fam]) agg[fam]={power:fam,n:0,sum:0,adjSum:0,hard:0,bad:0,best:null,bestScore:-1e9,members:{}};
    const a=agg[fam];
    if(!a.members[p]) a.members[p]={power:p,n:0,sum:0};
    a.members[p].n+=1; a.members[p].sum+=(r.composite||0);
    a.n+=1; a.sum+=(r.composite||0); a.adjSum+=(r.adj||0);
    if(r.hard&&r.hard.length) a.hard+=1;
    const vs=r.verdicts||{};
    for(const k of Object.keys(vs)){ if(vs[k]&&vs[k][0]==='不建议') a.bad+=1; }
    if((r.composite||0)>a.bestScore){ a.best=r.model; a.bestScore=r.composite||0; }
  }
  const out=Object.keys(agg).map(p=>{
    const a=agg[p];
    const members=Object.keys(a.members).map(k=>({power:k,cn:FORM_CN[k]||k,n:a.members[k].n,
      avg:Math.round((a.members[k].sum/a.members[k].n)*10)/10})).sort((x,y)=>y.avg-x.avg);
    return {power:p,n:a.n,avg:Math.round((a.sum/a.n)*10)/10,
            avg_adj:Math.round((a.adjSum/a.n)*100)/100,
            hard:a.hard,bad:a.bad,best:a.best,members:members};
  });
  out.sort((x,y)=> (y.avg-x.avg) || (x.hard-y.hard) || (y.n-x.n));
  const eligible=out.filter(x=>x.n>=FORM_MIN_N);
  const base=eligible.length?eligible[0]:out[0];
  out.forEach(x=>{
    if(x.n<FORM_MIN_N){ x.label='样本不足'; x.rank=null; return; }
    x.rank=eligible.indexOf(x)+1;
    if(x.power===base.power) x.label='最适配';
    else if(x.avg>=base.avg-1.5 && x.hard<=base.hard) x.label='可考虑';
    else if(x.avg>=base.avg-4) x.label='次选';
    else x.label='不建议';
  });
  // 展示顺序：有样本的先按名次，样本不足的排后（避免 n=2 的形式霸占第一名造成误读）
  out.sort((x,y)=>((x.rank?0:1)-(y.rank?0:1)) || ((x.rank||0)-(y.rank||0)) || (y.avg-x.avg));
  return out;
}
function formNarrative(forms,cond){
  const tag=f=>formName(f.power);
  const memNote=f=>{
    const mem=f.members||[];
    if(mem.length<=1) return '';
    return '（家族内部：'+mem.map(m=>m.cn+' '+m.avg.toFixed(1)+'（'+m.n+' 款）').join('、')+'）';
  };
  if(!forms||!forms.length) return '池内无车，无法给出形式结论';
  if(forms.length===1){const f0=forms[0];
    return '池内只有'+formName(f0.power)+'一种能源形式（'+f0.n+' 款），不存在跨形式取舍，形式适配结论不适用。';}
  const eligible=forms.filter(f=>f.n>=FORM_MIN_N);
  const pool=eligible.length?eligible:forms;
  const top=pool[0], second=pool[1];
  let txt='本画像下'+tag(top)+'形式最合适（均分 '+top.avg.toFixed(1)+'，池内 '+top.n+' 款，最强一款 '+top.best+'）';
  if(second){
    const gap=Math.round((top.avg-second.avg)*10)/10;
    if(gap>=2) txt+='，比'+tag(second)+'形式（均分 '+second.avg.toFixed(1)+'）明显领先 '+gap.toFixed(1)+' 分';
    else if(gap>0) txt+='，与'+tag(second)+'形式（均分 '+second.avg.toFixed(1)+'）只差 '+gap.toFixed(1)+' 分，按具体车型选更靠谱';
    else txt+='，与'+tag(second)+'基本同级';
  }
  const thin=forms.filter(f=>f.n<FORM_MIN_N);
  if(thin.length) txt+='；'+thin.map(f=>formName(f.power)+f.n+'款').join('、')+'样本不足，不作形式结论';
  const note=memNote(top)+(second?memNote(second):'');
  if(note) txt+='；'+note+'差距不构成「独立能源形式」的差别，逐车仍看具体车型';
  if(top.hard) txt+='；但该形式有 '+top.hard+' 款触发硬条件（无固定充电/极寒/长途续航等），需逐车核';
  return txt+'。';
}
function checkProfile(cond){
  const w=[];
  const noFixed=cond.home_charger==='none';
  const cold=(cond.winter_temp==='m20tom10'||cond.winter_temp==='underm20');
  const longNeed=(cond.long_trip_freq==='weekly'||cond.long_trip_freq==='monthly'||cond.single_trip==='over500');
  if(cond.operation==='yes'){
    if(longNeed) w.push({level:'warn',text:'营运车（网约/出租）极少单程 500km 以上的高频长途：确认是营运用途还是私家长途'});
    if(cond.hold_years==='over8') w.push({level:'warn',text:'营运车年里程常在 5 万公里以上，多数城市网约车 8 年强制退出：『8 年以上持有』与营运不匹配'});
    if(cond.fleet==='second') w.push({level:'warn',text:'营运车是生产工具，通常不是家庭第二辆车：两项条件相互矛盾'});
  }
  if(noFixed&&cold&&longNeed) w.push({level:'warn',text:'无固定充电 + 低温 + 高频长途：纯电在本画像下基本无解（H1/H2/H4 会连续触发），建议直接看插混/油混/燃油'});
  else if(noFixed&&cold) w.push({level:'info',text:'无固定充电 + 低温：纯电要靠公充与热泵/电池预热车型硬扛，先确认单位或住处能否装桩'});
  else if(noFixed&&longNeed) w.push({level:'info',text:'无固定充电 + 高频长途：纯电补能时间成本高，可油可电更稳'});
  if((cond.annual_km==='under5'||cond.annual_km==='under10')&&noFixed) w.push({level:'info',text:'年里程 1 万以下（尤其 5000km 以下）又无固定充电：纯电的能源节省很难覆盖保费溢价与残值差，油车/油混通常更省'});
  if(cond.fleet==='second'&&longNeed) w.push({level:'info',text:'增购第二辆 + 长途高频：长途交给已有油车即可，纯电按日常通勤里程选电池大小就行'});
  if(cond.hold_years==='under3'&&noFixed) w.push({level:'info',text:'3 年内换车 + 无固定充电：纯电折旧与补能双重不利，短期持有优先油车/油混'});
  if(cond.annual_km==='over25'&&cond.single_trip==='under150'&&cond.long_trip_freq==='rare') w.push({level:'info',text:'年里程 2.5 万以上但几乎全是短途：典型城配/网约特征，纯电最合适'});
  return w;
}

