/* ============================================================================
 * engine.js —— 跨能源「环境分 + 实际续航」引擎（唯一事实源）
 *
 * 这是 modules/envscore 的核心：给一台车 + 一套用车条件，算出
 *   1) rangeCalc / effRange：真实续航（用 PHYS 物理常数，服务端与浏览器同源）
 *   2) adjust：环境分（12 项用车条件的软加减分 + 硬闸门 hard 标记）
 *   3) compositeTier：按综合分划 T1/T2/T3
 *
 * 依赖注入（构建时由 build_compare.py 写入，不要在本文件里写死）：
 *   PHYS  —— 物理常数对象，来自 modules/physics/ev_range_model.py
 *   （车列表 CARS、条件表 FACTORS 由页面注入，本文件只消费）
 *
 * 纪律：本文件不放任何 DOM 操作。页面渲染（render/resetAll）留在
 * build_energy_compare.py 的模板里；node 回归只跑本文件的纯函数。
 * ========================================================================== */

// ===== 物理续航模型 v5 =====
// 基准电耗：真实电耗优先，缺失用物理回归(重量+CdA)估算 —— 已在 load_cars 算好 car.cons
// 工况倍率：高速按每车 CdA/吨 差异化（风阻大的高速更费电，解释方盒子效应）
// 再加入：温度、驾驶风格、空调、电压平台/碳化硅修正
const DEMAND={under150:100,'150to300':220,'300to500':400,over500:600};
// 档位：按当前榜内综合分相对位置分 T1/T2/T3（硬条件触发时在 render 里再降一档）
function compositeTier(score){ return score>=82?'T1':(score>=72?'T2':'T3'); }

function hwyMult(car,cond){
  // 高速倍率基准1.70，按 CdA/吨 偏离中位数修正；方盒子额外加成
  let r=car.cdaPerTon;
  if(cond.boxy==='yes'){
    // 方盒子：风阻按同级典型方盒子 Cd≈0.32、迎风面积偏大约20% 修正
    if(car.cd!=null) r=(car.cd*1.15*(car.area||2.6)*1.0)/((car.weight||1800)/1000);
    else r=PHYS.medRatio*1.5;
  }
  if(r==null) r=PHYS.medRatio;
  let m=PHYS.hwyBase*(1+0.45*(r/PHYS.medRatio-1));
  return Math.max(1.35,Math.min(2.35,m));
}

function rangeCalc(car,cond){
  if(car.power!=='BEV') return null;
  const hwWeight={under20:0.0,'20to40':0.25,'40to60':0.7,over60:1.0}[cond.highway_ratio];
  const hwM=hwyMult(car,cond);
  // 市区/高速倍率按高速占比加权
  const sceneM=PHYS.cityBase*(1-hwWeight)+hwM*hwWeight;
  const tempM=PHYS.temp[cond.winter_temp];  // 仅电池活性，不含暖风
  const driveM=PHYS.drive[cond.drive_style]||1.0;
  // 空调=季节基线×用户档位（温度与暖风拆分，避免旧版双重计算）
  const acM=(PHYS.acSeason[cond.winter_temp]||1.0)*(PHYS.ac[cond.ac_usage]||1.0);
  // 电压平台修正：800V+SiC 转换效率略高(影响小，主要影响充电速度)
  let voltM=1.0;
  if(car.voltage){
    const vs=Object.keys(PHYS.volt).map(Number).sort((a,b)=>a-b);
    let best=vs[0]; for(const v of vs) if(Math.abs(v-car.voltage)<Math.abs(best-car.voltage)) best=v;
    voltM=PHYS.volt[best];
  }
  const sceneCons=car.cons*sceneM*tempM*driveM*acM*voltM;
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
  steps.push('× 温度(仅电池) '+tempM+' × 驾驶 '+driveM+' × 空调 '+acM.toFixed(2)+'（季节基线'+PHYS.acSeason[cond.winter_temp]+'×档位'+(PHYS.ac[cond.ac_usage]||1)+'） × 电压系数 '+voltM+(car.voltage?'（'+car.voltage+'V平台）':''));
  steps.push('= 场景电耗 '+sceneCons.toFixed(1)+' kWh/100km');
  steps.push('电量 '+car.kwh+' kWh ÷ 场景电耗 ×100 = 有效续航 '+rng+' km');
  return {range:rng, method:car.consSrc==='事实库'?'物理模型(真实电耗基准)':'物理模型(估算电耗基准)',
          cls:car.boxy==='yes'?'方盒子':car.body, sceneCons:Math.round(sceneCons*10)/10, steps};
}
function effRange(car,cond){ const r=rangeCalc(car,cond); return r?r.range:null; }

// 场景调节：对具体车型计算，返回各项明细与合计（未缩放）
function adjust(car,cond){
  const items=[]; let total=0;
  const hard=[]; // 硬条件：{label,pt,reason,downgrade}
  const add=(label,pt)=>{ if(pt){items.push({label,pt}); total+=pt;} };
  const addHard=(label,pt,reason,downgrade)=>{ if(pt){hard.push({label,pt,reason,downgrade:!!downgrade}); total+=pt;} };
  const P=car.power;
  const isNew=(P==='BEV'||P==='PHEV'||P==='EREV');
  const isSwap=car.swap===true;

  // ===== 硬条件（大扣分 + 降档 + 标注）=====
  // H1: 无固定充电 + 纯电（非换电）→ 大扣分，换电车型缓解
  if(cond.home_charger==='none' && P==='BEV'){
    if(isSwap){
      addHard('无家充·换电缓解', PHYS.swapAdv.no_charger.gain, PHYS.swapAdv.no_charger.reason, false);
    }else{
      addHard('无充电桩·纯电不推荐', -15, '无固定充电，纯电日常补能困难、体验大幅打折；换电车型不受此限', true);
    }
  }
  // H2: 东北极寒 + 低价纯电（≤15万）→ 续航低+充电慢，谨慎；换电/大电池缓解
  if(cond.winter_temp==='underm20' && P==='BEV' && car.price!=null && car.price<=15){
    if(isSwap){
      addHard('极寒·换电缓解', PHYS.swapAdv.cold.gain, PHYS.swapAdv.cold.reason, false);
    }else{
      addHard('极寒+低价纯电·谨慎', -12, '-20℃以下低价纯电续航常腰斩、充电变慢，北方选购需谨慎；换电或大电池车型更稳', true);
    }
  }else if(cond.winter_temp==='underm20' && P==='BEV' && isSwap){
    addHard('极寒·换电优势', PHYS.swapAdv.cold.gain, PHYS.swapAdv.cold.reason, false);
  }
  // H3: 短持有(2-3年) + 纯电 → 折旧快；换电车型(车电分离)合适
  if(cond.hold_years==='under3' && P==='BEV'){
    if(isSwap){
      addHard('短持有·换电合适', PHYS.swapAdv.short_hold.gain, PHYS.swapAdv.short_hold.reason, false);
    }else{
      addHard('短持有·纯电折旧快', -8, '2-3年换车，纯电折旧快、残值损失大；换电车型车电分离更合适', true);
    }
  }
  // H4: 长途高频 + 纯电续航明显不足 → 大扣分
  if((cond.long_trip_freq==='weekly'||cond.long_trip_freq==='monthly') && P==='BEV'){
    const eff=effRange(car,cond), d=DEMAND[cond.single_trip];
    if(eff!=null && eff < d*0.75){
      if(isSwap) addHard('长途+换电缓解', PHYS.swapAdv.longtrip.gain, PHYS.swapAdv.longtrip.reason, false);
      else addHard('长途续航不足·不推荐', -10, '长途频繁且单次里程远超有效续航，纯电补能时间成本高', true);
    }
  }
  // H5: 限牌 + 燃油/油混 → 上牌困难
  if(cond.plate_limit==='yes' && (P==='ICE'||P==='HEV')){
    addHard('限牌城市·上牌难', -8, '限牌城市燃油/油混需摇号拍牌，新能源可直接上牌', true);
  }
  // H6: 营运 + 燃油/油混 → 成本劣势
  if(cond.operation==='yes' && (P==='ICE'||P==='HEV')){
    addHard('营运·能耗成本高', -10, '营运高频，燃油/油混能耗成本远高于纯电', true);
  }

  // ===== 常规软调节 =====
  // 1) 续航匹配（仅纯电）
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
  // 2) 长途频率（软）
  if(cond.long_trip_freq==='monthly'||cond.long_trip_freq==='weekly'){
    add('长途频率高', P==='BEV'?-1.5 : (P==='ICE'||P==='HEV')?1.5 : 0);
  }else if(cond.long_trip_freq==='rare'){ add('几乎无长途', P==='BEV'?1:0); }
  // 3) 充电条件（软，硬条件H1已覆盖纯电无桩）
  if(cond.home_charger==='home') add('有家充', P==='BEV'?1.5 : (P==='PHEV'||P==='EREV')?1 : 0);
  if(cond.home_charger==='none' && P!=='BEV') add('无固定充电', (P==='PHEV'||P==='EREV')?-1.5 : 1);
  // 4) 营运（软）
  if(cond.operation==='yes') add('营运', P==='BEV'?2 : 0);
  // 5) 高速占比（软）
  if(cond.highway_ratio==='over60') add('常年高速', P==='EREV'?-2 : P==='BEV'?-1 : 1);
  else if(cond.highway_ratio==='40to60') add('高速较多', P==='EREV'?-1:0);
  else if(cond.highway_ratio==='under20') add('市区为主', P==='BEV'?1:0);
  // 6) 方盒子 + 高频高速
  if(cond.boxy==='yes'&&cond.highway_ratio==='over60') add('方盒子+高频高速', P==='EREV'?-1.5 : P==='BEV'?-1.5 : P==='PHEV'?-1:0);
  // 7) 年里程
  if(cond.annual_km==='over25') add('高里程', P==='BEV'?1.5 : P==='ICE'?-1:0);
  else if(cond.annual_km==='under10') add('低里程', P==='BEV'?-1 : ((P==='ICE'||P==='HEV')?1:0));
  // 8) 持有年限（长期持有：电车能源节省累积、残值摊薄 → 纯电加分；与 under3 硬条件方向对称）
  //    over8 的 hint 明确写"纯电加分"，此前引擎漏实现导致该选项完全无效（回归探测抓出）。
  if(cond.hold_years==='over8') add('长期持有·电车收益累积', P==='BEV'?1.0 : (P==='PHEV'||P==='EREV')?0.3 : ((P==='ICE'||P==='HEV')?-0.3:0));

  return {items,total,hard};
}
