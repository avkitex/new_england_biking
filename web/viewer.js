// Public, read-only mode. Loads one static file by a relative URL and knows nothing about rides, dates or Strava.
window.MODE={
 async load(){
  const response=await fetch(new URL('data.json',document.baseURI));
  if(!response.ok)throw new Error('data.json is unavailable');
  const data=await response.json();
  if(data.schema_version!==2||data.kind!=='public')throw new Error('Unsupported map data format');
  return data;
 },
 trailInfo(c,p){
  const {esc,fmt,surfaces,statusLabels}=c;
  return `<b>${esc(p.name)}</b><br>${(p.states||[p.state]).join(' / ')} · ${fmt(p.km)} mi · ${surfaces[p.surface]}${p.surface_detail?' ('+esc(c.vlabel('surface',p.surface_detail))+')':''}<br>${p.ridden_km>0?`<strong>Ridden: ≈ ${fmt(p.ridden_km)} mi (${Math.round(p.coverage*100)}%)</strong><br>`:''}${statusLabels[p.status]}<br><a href="${esc(p.url)}" target="_blank" rel="noopener">Source: ${esc(p.source)} ↗</a>`;
 },
 drawOverlay(c,{matches,gps,matched}){
  if(gps&&c.DATA.coverage){c.add(c.DATA.coverage,{color:'#fff',weight:6,opacity:.95},'gpshalo');c.add(c.DATA.coverage,{color:'#cc1667',weight:3,opacity:.9,dashArray:'5 5'},'gpslines');}
  if(matched){c.add(matches,{color:'#fff',weight:10,opacity:1},'matchhalo');c.add(matches,{color:'#cc1667',weight:6,opacity:1},'matchlines');}
 },
 stats(c){return `${c.fmt(c.DATA.stats.coverage_km)} mi of tracks ridden · six-state catalog`;},
 fitRides(c){return c.DATA.coverage?[c.DATA.coverage]:[];}
};
