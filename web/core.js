// Shared map core. A mode script (editor.js or viewer.js) must define window.MODE first.
(async()=>{
const MODE=window.MODE;
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const surfaces={paved:'Paved',unpaved:'Gravel / unpaved',unknown:'Unknown'};
const statusLabels={existing:'Existing according to source',catalog:'Listed in catalog; check access',planned:'Planned / construction according to source'};
const stateNames={MA:'Massachusetts',NH:'New Hampshire',VT:'Vermont',ME:'Maine',CT:'Connecticut',RI:'Rhode Island'};
const fmt=n=>(n/1.609344).toLocaleString('en-US',{maximumFractionDigits:1});
const DATA=await MODE.load($);
const map=L.map('map',{preferCanvas:false,zoomAnimation:false,fadeAnimation:false}).setView([43.6,-70.7],7);
if(location.protocol==='file:'){$('baseError').style.display='block';$('baseError').textContent='The basemap needs the page to be served over http(s).';}
else if(!new URLSearchParams(location.search).has('offline')){let errors=0;L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,keepBuffer:1,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · Trailspotting · MassGIS · NHDOT · VTrans · RIDOT/RIGIS · CT DEEP'}).on('tileerror',()=>{if(++errors>=3)$('baseError').style.display='block'}).addTo(map);}
for(const [id,z] of [['areas',405],['catalog',410],['overlay',415],['gpshalo',420],['gpslines',430],['matchhalo',440],['matchlines',450],['blocked',455],['selected',460],['tags',470]]){map.createPane(id);map.getPane(id).style.zIndex=z;if(['selected','gpshalo','matchhalo'].includes(id))map.getPane(id).style.pointerEvents='none';}
const ctx={$,esc,fmt,map,DATA,surfaces,statusLabels,stateNames,layers:[],active:[],selectedLayer:null};
const VOCAB=DATA.vocabulary||{fields:{},tags:[]};
const fieldValues=f=>VOCAB.fields[f]?.values||[];
ctx.vlabel=(field,id)=>fieldValues(field).find(v=>v.id===id)?.label||'';
const CLASS_COLORS={paved:'#2f6fa8',unpaved:'#c1561e',unknown:'#8794a0'};
const valueColor=(field,id)=>fieldValues(field).find(v=>v.id===id)?.color;
const tagById=id=>VOCAB.tags.find(t=>t.id===id);
const catById=new Map(DATA.catalog.features.map(f=>[f.properties.id,f]));
let tagTrails=new Map();
function rebuildTagTrails(){tagTrails=new Map(VOCAB.tags.map(t=>[t.id,new Set()]));for(const f of (DATA.overlays?.features||[]))for(const t of f.properties.tags||[])if(f.properties.trail!=null)tagTrails.get(t)?.add(f.properties.trail);}
rebuildTagTrails();
for(const k of Object.keys(VOCAB.fields))$('colorBy').add(new Option(VOCAB.fields[k].label,k));
for(const t of VOCAB.tags)$('tagFilter').add(new Option(t.icon+' '+t.label,t.id));
ctx.setOverlays=(overlays,surfaces)=>{DATA.overlays=overlays;if(surfaces)for(const f of DATA.catalog.features){const s=surfaces[f.properties.id];if(s){f.properties.surface=s[0];if(s[1])f.properties.surface_detail=s[1];else delete f.properties.surface_detail;}}rebuildTagTrails();};
ctx.vocab=VOCAB;ctx.valueColor=valueColor;
ctx.setSelected=layer=>{if(ctx.selectedLayer)map.removeLayer(ctx.selectedLayer);ctx.selectedLayer=layer;};
const routeKey=f=>f.properties.name.toLowerCase()+'|'+f.properties.state;
function combine(fs){const p={...fs[0].properties};p.km=fs.reduce((s,f)=>s+f.properties.km,0);p.ridden_km=fs.reduce((s,f)=>s+f.properties.ridden_km,0);p.coverage=p.km?p.ridden_km/p.km:0;p.rides=[...new Set(fs.flatMap(f=>f.properties.rides||[]))];p.states=[...new Set(fs.flatMap(f=>f.properties.states||[f.properties.state]))];p.source=[...new Set(fs.map(f=>f.properties.source))].join(' + ');p.surface=new Set(fs.map(f=>f.properties.surface)).size===1?p.surface:'unknown';return {type:'Feature',properties:p,geometry:{type:'MultiLineString',coordinates:fs.flatMap(f=>f.geometry.type==='LineString'?[f.geometry.coordinates]:f.geometry.coordinates)}};}
function eligible(f){const p=f.properties;return (!p.name.includes('Unnamed')||p.ridden_km>0||$('unnamed').checked)&&(!$('state').value||(p.states||[p.state]).includes($('state').value))&&(!$('search').value||p.name.toLowerCase().includes($('search').value.toLowerCase()))&&(!$('surface').value||p.surface===$('surface').value)&&(!$('visited').value||($('visited').value==='yes'?p.ridden_km>0:p.ridden_km===0))&&(p.status!=='planned'||$('planned').checked)&&(p.category!=='lane'||$('lanes').checked)&&(p.category!=='mtb'||$('mtb').checked)&&(!$('tagFilter').value||tagTrails.get($('tagFilter').value)?.has(p.id));}
function selected(f,latlng){
 const style={color:'#f7941d',weight:10,opacity:$('colorBy').value?.28:.85};
 const parts=f.geometry.type==='MultiLineString'?f.geometry.coordinates:null;
 let layer,note='';
 if(parts&&parts.length>1&&latlng){
  // One catalog record can be several separate pieces, sometimes kilometres apart: highlight the piece that was clicked, show the rest faintly.
  const d=pt=>(pt[1]-latlng.lat)**2+(pt[0]-latlng.lng)**2;
  let best=0,bd=1e18;parts.forEach((cs,i)=>{for(const pt of cs){const v=d(pt);if(v<bd){bd=v;best=i;}}});
  const line=cs=>({type:'Feature',properties:f.properties,geometry:{type:'LineString',coordinates:cs}});
  layer=L.layerGroup([L.geoJSON(parts.filter((_,i)=>i!==best).map(line),{pane:'selected',style:{color:'#f7941d',weight:5,opacity:.35,dashArray:'2 8'},interactive:false}),L.geoJSON(line(parts[best]),{pane:'selected',style,interactive:false})]).addTo(map);
  note=`<br><span class="note">This record is ${parts.length} separate pieces; the others are shown faintly.</span>`;
 }else layer=L.geoJSON(f,{pane:'selected',style,interactive:false}).addTo(map);
 ctx.setSelected(layer);$('detail').innerHTML=MODE.trailInfo(ctx,f.properties)+note;
}
// onEach(feature, path) makes a layer interactive; omit it for a passive layer.
ctx.add=(fs,style,pane,onEach)=>{const l=L.geoJSON(fs,{pane,style,interactive:!!onEach,onEachFeature:onEach?(f,path)=>{path.options.bubblingMouseEvents=false;onEach(f,path);}:undefined}).addTo(map);ctx.layers.push(l);return l;};
ctx.fitTo=fs=>{if(fs.length)map.fitBounds(L.geoJSON(fs).getBounds(),{padding:[35,35]});};
function overlayInfo(p){const rows=Object.keys(VOCAB.fields).filter(k=>p[k]).map(k=>`${esc(VOCAB.fields[k].label)}: ${esc(ctx.vlabel(k,p[k]))}`);if(p.tags?.length)rows.push(p.tags.map(t=>{const x=tagById(t);return x?esc(x.icon+' '+x.label):''}).join(', '));if(p.comment)rows.push(esc(p.comment));return rows.join('<br>')||'No details.';}
function syncTags(){if(!ctx.tagLayer)return;const show=map.getZoom()>=11;if(show&&!map.hasLayer(ctx.tagLayer))ctx.tagLayer.addTo(map);if(!show&&map.hasLayer(ctx.tagLayer))map.removeLayer(ctx.tagLayer);}
map.on('zoomend',syncTags);
function draw(rebuild=true,keepView=false){
 ctx.layers.forEach(l=>map.removeLayer(l));ctx.layers=[];ctx.tagLayer=null;ctx.setSelected(null);
 const fs=DATA.catalog.features.filter(eligible);
 if(rebuild){const prev=$('trail').value;$('trail').length=1;const unique=[...new Map(fs.map(f=>[routeKey(f),f])).values()];unique.sort((a,b)=>a.properties.name.localeCompare(b.properties.name)).forEach(f=>$('trail').add(new Option(`${f.properties.name} · ${(f.properties.states||[f.properties.state]).join(' / ')}`,routeKey(f))));if(fs.some(f=>routeKey(f)===prev))$('trail').value=prev;}
 ctx.active=fs.filter(f=>!$('trail').value||routeKey(f)===$('trail').value);
 const ids=new Set(ctx.active.map(f=>f.properties.id));
 const matches=DATA.matched.features.filter(f=>ids.has(f.properties.id));
 const by=$('colorBy').value;
 const baseColor=f=>{const p=f.properties;if(by==='surface')return CLASS_COLORS[p.surface]||CLASS_COLORS.unknown;if(by)return '#aab5bd';return p.status==='planned'?'#c17e22':'#596d80';};
 const baseDash=f=>f.properties.status==='planned'?'7 6':(f.properties.surface==='unpaved'?'3 5':null);
 if($('known').checked){
  ctx.add(ctx.active,{color:'#fff',weight:6,opacity:.95},'catalog');
  ctx.add(ctx.active,f=>({color:baseColor(f),weight:3,opacity:.95,dashArray:baseDash(f)}),'catalog',(f,path)=>{if(!MODE.onTrailClick)path.bindPopup(MODE.trailInfo(ctx,f.properties));path.bindTooltip(esc(f.properties.name),{sticky:true});path.on('click',e=>{if(MODE.onTrailClick)MODE.onTrailClick(ctx,f,e.originalEvent);else selected(f,e.latlng);});});
  const allOv=(DATA.overlays?.features||[]).filter(f=>(f.properties.trail==null||ids.has(f.properties.trail))&&(!$('tagFilter').value||(f.properties.tags||[]).includes($('tagFilter').value)));
  const isArea=f=>/Polygon/.test(f.geometry.type),ov=allOv.filter(f=>!isArea(f)),areas=allOv.filter(isArea);
  if(areas.length)ctx.add(areas,{color:'#2b9be8',weight:2,dashArray:'6 4',fillColor:'#4fb3ff',fillOpacity:.25},'areas',(f,path)=>{if(MODE.onItemClick)path.on('click',e=>MODE.onItemClick(f.properties.item,e.originalEvent));else path.bindPopup('<b>Not bikeable area</b><br>'+overlayInfo(f.properties));});
  const paint=f=>(by&&valueColor(by,f.properties[by]))||'#596d80';
  const isNo=f=>f.properties.passability==='no';
  const colored=ov.filter(f=>!isNo(f)&&((by&&valueColor(by,f.properties[by]))||f.properties.trail==null));
  const onOv=(f,path)=>{const tf=catById.get(f.properties.trail);if(MODE.onItemClick&&f.properties.item)path.on('click',e=>MODE.onItemClick(f.properties.item,e.originalEvent));else if(MODE.onTrailClick&&tf)path.on('click',e=>MODE.onTrailClick(ctx,tf,e.originalEvent));else if(!MODE.onTrailClick)path.bindPopup(overlayInfo(f.properties));};
  if(colored.length)ctx.add(colored,f=>({color:paint(f),weight:5,opacity:1}),'overlay',onOv);
  // Not rideable: light-blue dashed line on a white halo, above everything, clearly unlike the pink ridden tracks.
  const blocked=ov.filter(isNo);
  if(blocked.length){ctx.add(blocked,{color:'#fff',weight:10,opacity:.9},'blocked');ctx.add(blocked,{color:'#4fb3ff',weight:6,opacity:1,dashArray:'9 6'},'blocked',onOv);}
  ctx.tagLayer=L.layerGroup();
  for(const f of ov.filter(f=>(f.properties.tags||[]).length)){const cs=f.geometry.type==='LineString'?f.geometry.coordinates:f.geometry.coordinates[0];const m=cs[Math.floor(cs.length/2)];
   const icon=L.divIcon({className:'tagicon',html:f.properties.tags.slice(0,3).map(t=>tagById(t)?.icon||'').join(''),iconSize:null});
   L.marker([m[1],m[0]],{icon,pane:'tags',keyboard:false}).bindPopup(overlayInfo(f.properties)).addTo(ctx.tagLayer);}
  ctx.layers.push(ctx.tagLayer);syncTags();
 }
 $('legendBy').innerHTML=by?(fieldValues(by).map(v=>`<span class="sw" style="background:${v.color};height:5px;width:18px"></span>${esc(v.label)}`).join('<br>')):'';
 MODE.drawOverlay(ctx,{ids,matches,gps:$('gps').checked,matched:$('matched').checked});
 $('catalogCount').textContent=`${ctx.active.length} catalog records · ${matches.length} with GPS matches`;
 $('stats').textContent=MODE.stats(ctx);
 if(MODE.afterDraw)MODE.afterDraw(ctx);
 if($('trail').value&&ctx.active.length){if(!MODE.onTrailClick)selected(combine(ctx.active));if(!keepView)map.fitBounds(L.geoJSON(ctx.active).getBounds(),{padding:[35,35]});}
}
ctx.draw=draw;
// Public page: click the map background (or press Escape) to drop the highlighted trail. The editor has its own selection.
if(!MODE.onTrailClick){
 const detail0=$('detail').innerHTML;
 const deselect=()=>{ctx.setSelected(null);$('detail').innerHTML=detail0;map.closePopup();};
 map.on('click',deselect);
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName))deselect();});
}
const fit=()=>{if(ctx.active.length)map.fitBounds(L.geoJSON(ctx.active).getBounds(),{padding:[30,30]});};
const NE=[[40.9,-73.8],[47.5,-66.8]];
for(const id of ['known','matched','gps','unnamed','planned','lanes','mtb','visited','surface','colorBy','tagFilter'])$(id).onchange=()=>draw();
$('state').onchange=()=>{$('trail').value='';if(MODE.onState)MODE.onState(ctx);draw();map.fitBounds(NE,{padding:[15,15]});fit();};
$('trail').onchange=()=>{draw(false);if(MODE.onTrailPicked)MODE.onTrailPicked(ctx);};
let timer;$('search').oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>{$('trail').value='';draw()},180)};
$('fit').onclick=fit;
$('myrides').onclick=()=>ctx.fitTo(MODE.fitRides(ctx));
$('contrast').oninput=()=>document.querySelector('.leaflet-tile-pane').style.opacity=$('contrast').value/100;
if(MODE.init)MODE.init(ctx);
draw();map.fitBounds(NE,{padding:[15,15]});
if(MODE.ready)MODE.ready(ctx);
})().catch(e=>{document.getElementById('stats').textContent='Cannot load map data: '+e.message;});
