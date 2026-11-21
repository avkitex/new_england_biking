// Local editor mode: full private snapshots + trail selection and bulk annotation editing (server.py).
(()=>{
let ctx,manifest,DATA;
const $=id=>document.getElementById(id);
const sel=new Map();            // trail id -> catalog feature
let range=null;                 // {id,a:[lon,lat],b:[lon,lat]} when editing part of one trail
let pick=0,pickPoints=[];       // 0 off; 1 waiting for first point; 2 waiting for second
let selLayer=null,pickLayer=null,token='',revision='',busy=false,justBoxed=false;
const byId=new Map();
let lineTool='area';           // what the line being drawn is for: 'blocked' or 'corridor'
let lastSeg=null;                // last GPS section clicked (for Alt-click ranges)
let lineDraw=null;              // points of the blocked section being drawn, or null
let linePreview=null;
const roadSel=new Map();        // GPS section id -> feature (ids are only ever sent to the local server)
const itemSel=new Set();        // ids of existing road annotations
const overlayItems=id=>(DATA.overlays?.features||[]).filter(f=>f.properties.item===id);
const visibleSegments=()=>{const ids=new Set(rideFeatures().map(r=>r.properties.id));return DATA.note_segments.features.filter(f=>f.properties.kind==='ride'&&ids.has(f.properties.parent_id));};
const vertices=f=>f.geometry.type==='LineString'?f.geometry.coordinates:f.geometry.coordinates.flat();
const FIELDS=()=>Object.keys(ctx.vocab.fields);

function rideFeatures(){return DATA.rides.features.filter(f=>(!$('state').value||f.properties.states.includes(ctx.stateNames[$('state').value]))&&(!$('ride').value||f.properties.id===$('ride').value));}
function updateRides(){const prev=$('ride').value;$('ride').length=1;DATA.rides.features.filter(f=>!$('state').value||f.properties.states.includes(ctx.stateNames[$('state').value])).forEach(f=>$('ride').add(new Option(`${f.properties.date} · ${ctx.fmt(f.properties.km)} mi`,f.properties.id)));if([...$('ride').options].some(o=>o.value===prev))$('ride').value=prev;}
const status=t=>{$('annStatus').textContent=t;};

// ---------- selection ----------
function pieces(id){return (DATA.overlays?.features||[]).filter(f=>f.properties.trail===id);}
function renderSelection(){
 if(selLayer){ctx.map.removeLayer(selLayer);selLayer=null;}
 if(pickLayer){ctx.map.removeLayer(pickLayer);pickLayer=null;}
 const fs=[...sel.values(),...roadSel.values(),...[...itemSel].flatMap(overlayItems)];
 if(fs.length)selLayer=L.geoJSON(fs,{pane:'selected',style:{color:'#f7941d',weight:10,opacity:$('colorBy').value?.28:.85},interactive:false}).addTo(ctx.map);
 if(range){const f=sel.get(range.id);const cs=f.geometry.type==='LineString'?f.geometry.coordinates:f.geometry.coordinates.flat();
  const near=p=>{let k=0,best=1e18;cs.forEach((c,i)=>{const d=(c[0]-p[0])**2+(c[1]-p[1])**2;if(d<best){best=d;k=i}});return k};
  const i=near(range.a),j=near(range.b);const part=cs.slice(Math.min(i,j),Math.max(i,j)+1).map(c=>[c[1],c[0]]);
  pickLayer=L.polyline(part,{pane:'selected',color:'#7a1fa2',weight:12,opacity:.8,interactive:false}).addTo(ctx.map);}
 else if(pickPoints.length)pickLayer=L.layerGroup(pickPoints.map(p=>L.circleMarker([p[1],p[0]],{pane:'selected',radius:7,color:'#7a1fa2',fillOpacity:1}))).addTo(ctx.map);
 const km=[...sel.values()].reduce((s,f)=>s+f.properties.km,0),mi=[...roadSel.values()].reduce((s,f)=>s+f.properties.miles,0);
 const names=sel.size&&sel.size<=3?' · '+[...sel.values()].map(f=>f.properties.name).join(', '):'';
 $('selSummary').textContent=!fs.length?'Nothing selected.':[sel.size?`${sel.size} trail${sel.size>1?'s':''} · ${ctx.fmt(km)} mi${range?' (part of one)':''}${names}`:'',roadSel.size?`${roadSel.size} GPS section${roadSel.size>1?'s':''} · ${mi.toFixed(1)} mi`:'',itemSel.size?`${itemSel.size} annotated road${itemSel.size>1?'s':''}`:''].filter(Boolean).join(' · ');
 $('pickRange').disabled=sel.size!==1||roadSel.size>0||itemSel.size>0;$('pickRange').textContent=pick===1?'Click the first point on the trail…':pick===2?'Click the second point…':range?'Clear the part (whole trail)':'Pick part of the selected trail (2 clicks)';
 refreshControls();
}
function setSelection(features,add){
 if(!add){sel.clear();roadSel.clear();itemSel.clear();}
 for(const f of features)sel.set(f.properties.id,f);
 range=null;pick=0;pickPoints=[];renderSelection();
}
function toggle(f){range=null;pick=0;pickPoints=[];if(sel.has(f.properties.id))sel.delete(f.properties.id);else sel.set(f.properties.id,f);renderSelection();}
function clearSelection(){sel.clear();roadSel.clear();itemSel.clear();range=null;pick=0;pickPoints=[];renderSelection();map_closePopup();}
function map_closePopup(){ctx.map.closePopup();}

// ---------- controls ----------
function buildControls(){
 const box=$('fields');box.innerHTML='';
 for(const key of FIELDS()){
  const field=ctx.vocab.fields[key];
  const label=document.createElement('label');label.textContent=field.label;label.htmlFor='f_'+key;
  const s=document.createElement('select');s.id='f_'+key;
  s.add(new Option('— no change —',''));s.add(new Option('(clear → catalog default)','__clear'));
  for(const v of field.values)s.add(new Option(v.label,v.id));
  box.append(label,s);
 }
 const tags=$('tagsBox');tags.innerHTML='<label>Tags</label>';
 for(const t of ctx.vocab.tags){
  const l=document.createElement('label');l.className='toggle';
  const c=document.createElement('input');c.type='checkbox';c.dataset.tag=t.id;c.onchange=()=>{c.dataset.touched='1';c.indeterminate=false;};
  l.append(c,document.createTextNode(t.icon+' '+t.label));tags.append(l);
 }
 $('comment').oninput=()=>{$('comment').dataset.touched='1';};
}
// Existing annotated roads that lie under the selected GPS sections, so their values show in the controls.
function roadMatches(){
 const found=[];
 for(const f of (DATA.overlays?.features||[])){
  if(!f.properties.item||itemSel.has(f.properties.item)||/Polygon/.test(f.geometry.type))continue;
  const vs=vertices(f);if(!vs.length)continue;
  let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;for(const c of vs){if(c[0]<x0)x0=c[0];if(c[0]>x1)x1=c[0];if(c[1]<y0)y0=c[1];if(c[1]>y1)y1=c[1];}
  const kx=111320*Math.cos(vs[0][1]*Math.PI/180),ky=110540,pad=.0004;let hit=false;
  for(const g of roadSel.values()){
   const cs=vertices(g),step=Math.max(1,Math.floor(cs.length/6));
   for(let i=0;i<cs.length&&!hit;i+=step){const c=cs[i];if(c[0]<x0-pad||c[0]>x1+pad||c[1]<y0-pad||c[1]>y1+pad)continue;
    for(let k=1;k<vs.length&&!hit;k++){   // distance to each piece of the polyline, not just its vertices (GPS vertices can be far apart)
     const ax=(vs[k-1][0]-c[0])*kx,ay=(vs[k-1][1]-c[1])*ky,bx=(vs[k][0]-c[0])*kx,by=(vs[k][1]-c[1])*ky,dx=bx-ax,dy=by-ay,L=dx*dx+dy*dy;
     let t=L?(-ax*dx-ay*dy)/L:0;t=Math.max(0,Math.min(1,t));const ex=ax+t*dx,ey=ay+t*dy;if(ex*ex+ey*ey<225)hit=true;}}
   if(hit)break;
  }
  if(hit)found.push(f);
 }
 return found;
}
function refreshControls(){
 const groups=[...[...sel.keys()].map(pieces),...[...itemSel].map(overlayItems),...(roadSel.size?[roadMatches()]:[])];
 const count=groups.length;   // new GPS sections form one group with no values yet
 const ps=groups.flat();
 for(const key of FIELDS()){
  const values=new Set(ps.map(p=>p.properties[key]).filter(Boolean));
  const now=!count?'':values.size===0?'not set':values.size===1?ctx.vlabel(key,[...values][0]):'mixed';
  $('f_'+key).options[0].text=now?`— no change (now: ${now}) —`:'— no change —';$('f_'+key).value='';
 }
 for(const c of document.querySelectorAll('#tagsBox input')){
  const n=groups.filter(g=>g.some(p=>(p.properties.tags||[]).includes(c.dataset.tag))).length;
  c.checked=count>0&&n===count;c.indeterminate=n>0&&n<count;delete c.dataset.touched;
 }
 const comments=[...new Set(ps.map(p=>p.properties.comment).filter(Boolean))];
 $('comment').value='';delete $('comment').dataset.touched;
 $('comment').placeholder=comments.length?'Current: '+comments.join(' · ').slice(0,200)+' (type to replace; clear to remove)':'Leave untouched to keep existing comments';
 $('applyEdit').disabled=!count||busy||!token;
}
function buildEdit(){
 const edit={targets:[...(range?[{trail:range.id,a:range.a,b:range.b}]:[...sel.keys()].map(trail=>({trail}))),...(roadSel.size?[{roads:[...roadSel.keys()]}]:[]),...[...itemSel].map(item=>({item}))]};
 const set={};for(const key of FIELDS()){const v=$('f_'+key).value;if(v)set[key]=v==='__clear'?null:v;}
 if(Object.keys(set).length)edit.set=set;
 const add=[],remove=[];for(const c of document.querySelectorAll('#tagsBox input'))if(c.dataset.touched)(c.checked?add:remove).push(c.dataset.tag);
 if(add.length)edit.tags_add=add;if(remove.length)edit.tags_remove=remove;
 if($('comment').dataset.touched)edit.comment=$('comment').value;
 return Object.keys(edit).length>1?edit:null;
}
async function api(path,options){const r=await fetch(path,{cache:'no-store',...options});const d=await r.json();if(!r.ok)throw new Error(d.error||'Request failed');return d;}
function accept(d){revision=d.revision;if(d.token){token=d.token;ExcludeTool.setToken(token);}ctx.setOverlays(d.overlays,d.surfaces);ctx.draw(true,true);renderSelection();}
async function loadAnnotations(){
 try{const d=await api('/api/annotations');accept(d);status(`${d.count} annotation records${d.orphans?` · ${d.orphans} orphaned (catalog changed)`:''}`);}
 catch(e){token='';$('applyEdit').disabled=true;status('Cannot load annotations: '+e.message+'. Open the local map using Start Map.ps1.');}
}
async function apply(){
 const edit=buildEdit();if(!edit){status('Nothing to change.');return;}
 busy=true;$('applyEdit').disabled=true;status('Saving…');
 try{const d=await api('/api/annotations',{method:'POST',headers:{'Content-Type':'application/json','X-Notes-Token':token},body:JSON.stringify({revision,edit})});roadSel.clear();itemSel.clear();accept(d);status(`Saved · ${d.count} annotation records · ${new Date().toLocaleTimeString('en-US')}`);}
 catch(e){status('Not saved: '+e.message);}
 finally{busy=false;refreshControls();}
}

// ---------- draw a blocked section ----------
function lineLayer(){if(linePreview){ctx.map.removeLayer(linePreview);linePreview=null;}
 if(lineDraw&&lineDraw.length)linePreview=L.layerGroup([(lineTool==='area'&&lineDraw.length>2?L.polygon:L.polyline)(lineDraw.map(p=>[p[1],p[0]]),{pane:'selected',color:lineTool==='area'?'#c62d2d':'#2b7bd1',weight:3,dashArray:'6 6',fillOpacity:.2,interactive:false}),...lineDraw.map(p=>L.circleMarker([p[1],p[0]],{pane:'selected',radius:4,color:'#c62d2d',fillOpacity:1,interactive:false}))]).addTo(ctx.map);}
function startLine(tool){if(ExcludeTool.active()){status('Finish or cancel the removal drawing first.');return;}if(tool==='area')clearSelection();lineTool=tool;lineDraw=[];$('map').classList.add('drawing');$('drawBlocked').textContent=tool==='area'?'Done (Enter)':'Draw: non-bikeable area';$('alongBtn').textContent=tool==='corridor'?'Done (Enter)':'Select along a line';status(tool==='area'?'Click the corners of the area. Enter or double-click to save it as not bikeable.':'Click along the road you want to select. Enter or double-click to select every GPS section along it.');}
function addLinePoint(latlng){lineDraw.push([+latlng.lng.toFixed(6),+latlng.lat.toFixed(6)]);lineLayer();}
function cancelLine(){if(!lineDraw)return;lineDraw=null;$('map').classList.remove('drawing');$('drawBlocked').textContent='Draw: non-bikeable area';$('alongBtn').textContent='Select along a line';lineLayer();status('');}
async function finishLine(){
 if(!lineDraw)return;const pts=lineDraw;
 if(pts.length<(lineTool==='area'?3:2)){status(lineTool==='area'?'An area needs at least 3 corners.':'A line needs at least 2 points.');return;}
 if(lineTool==='corridor'){cancelLine();selectAlong(pts);return;}
 const edit={targets:[{area:pts}],set:{passability:'no'}};if($('comment').dataset.touched&&$('comment').value.trim())edit.comment=$('comment').value;
 cancelLine();busy=true;status('Saving…');
 try{const d=await api('/api/annotations',{method:'POST',headers:{'Content-Type':'application/json','X-Notes-Token':token},body:JSON.stringify({revision,edit})});accept(d);status(`Non-bikeable area saved · ${d.count} annotation records`);}
 catch(e){status('Not saved: '+e.message);}
 finally{busy=false;refreshControls();}
}

// ---------- select GPS sections along a drawn line ----------
function selectAlong(line){
 const width=Math.max(5,Math.min(200,+$('alongWidth').value||30));
 const lat0=line[0][1],kx=111320*Math.cos(lat0*Math.PI/180),ky=110540;
 const P=line.map(p=>[p[0]*kx,p[1]*ky]);
 const d2=(x,y)=>{let best=1e18;for(let i=1;i<P.length;i++){const [ax,ay]=P[i-1],[bx,by]=P[i];const dx=bx-ax,dy=by-ay,L=dx*dx+dy*dy;let t=L?((x-ax)*dx+(y-ay)*dy)/L:0;t=Math.max(0,Math.min(1,t));const ex=ax+t*dx-x,ey=ay+t*dy-y;const d=ex*ex+ey*ey;if(d<best)best=d;}return best;};
 const lo=[Math.min(...line.map(p=>p[0])),Math.min(...line.map(p=>p[1]))],hi=[Math.max(...line.map(p=>p[0])),Math.max(...line.map(p=>p[1]))];
 const pad=width/kx*1.5,padY=width/ky*1.5,w2=width*width;let n=0;
 if(!(ctx&&$('selRoads').checked===false)){
  for(const g of visibleSegments()){
   const cs=vertices(g);let near=0,tot=0;
   for(const c of cs){if(c[0]<lo[0]-pad||c[0]>hi[0]+pad||c[1]<lo[1]-padY||c[1]>hi[1]+padY){tot++;continue;}tot++;if(d2(c[0]*kx,c[1]*ky)<=w2)near++;}
   if(tot&&near/tot>=.5){roadSel.set(g.properties.segment_id,g);n++;}
  }
 }
 sel.clear();itemSel.clear();range=null;pick=0;pickPoints=[];
 renderSelection();status(n?`Selected ${n} GPS section${n>1?'s':''} within ${width} m of the line.`:'No GPS sections found along that line. Try a wider corridor or draw closer to the track.');
}

// ---------- map interaction ----------
function handlePick(latlng){
 const p=[latlng.lng,latlng.lat];pickPoints.push(p);
 if(pickPoints.length===2){const [id]=sel.keys();range={id,a:pickPoints[0],b:pickPoints[1]};pick=0;pickPoints=[];}
 else pick=2;
 renderSelection();
}
function plainOrAdd(ev,has,add,remove,only){
 if(ev.ctrlKey||ev.shiftKey||ev.metaKey){has?remove():add();return;}
 if(only){clearSelection();return;}
 sel.clear();range=null;pick=0;pickPoints=[];roadSel.clear();itemSel.clear();add();
}
let byRide=null;
function rideSegments(parent){
 if(!byRide){byRide=new Map();for(const g of DATA.note_segments.features)if(g.properties.kind==='ride'){const a=byRide.get(g.properties.parent_id)||[];a.push(g);byRide.set(g.properties.parent_id,a);}
  for(const a of byRide.values())a.sort((x,y)=>x.properties.number-y.properties.number);}
 return byRide.get(parent)||[];
}
function onSegmentClick(f,ev){
 if(ExcludeTool.active()||lineDraw)return;
 const id=f.properties.segment_id;
 if(ev.altKey&&lastSeg&&lastSeg.properties.parent_id===f.properties.parent_id){
  const [a,b]=[lastSeg.properties.number,f.properties.number].sort((x,y)=>x-y);
  for(const g of rideSegments(f.properties.parent_id))if(g.properties.number>=a&&g.properties.number<=b)roadSel.set(g.properties.segment_id,g);
  sel.clear();itemSel.clear();lastSeg=f;renderSelection();return;
 }
 lastSeg=f;
 plainOrAdd(ev,roadSel.has(id),()=>roadSel.set(id,f),()=>roadSel.delete(id),roadSel.size===1&&!itemSel.size&&!sel.size&&roadSel.has(id));renderSelection();
}
function onItemClick(id,ev){
 if(ExcludeTool.active()||lineDraw)return;
 plainOrAdd(ev,itemSel.has(id),()=>itemSel.add(id),()=>itemSel.delete(id),itemSel.size===1&&!roadSel.size&&!sel.size&&itemSel.has(id));renderSelection();
}
function onTrailClick(c,f,ev){
 if(ExcludeTool.active()||lineDraw)return;
 if(pick){handlePick(c.map.mouseEventToLatLng(ev));return;}
 $('detail').innerHTML=MODE_trailInfo(c,f.properties);
 if(ev.ctrlKey||ev.shiftKey||ev.metaKey)toggle(f);
 else if(sel.size===1&&sel.has(f.properties.id)&&!range&&!roadSel.size&&!itemSel.size)clearSelection();
 else setSelection([f],false);
}
function setupBox(){
 const m=ctx.map;m.boxZoom.disable();
 let start=null,rect=null;
 m.getContainer().addEventListener('mousedown',e=>{if(!e.shiftKey||e.button!==0)return;e.preventDefault();e.stopPropagation();start=m.mouseEventToLatLng(e);m.dragging.disable();rect=L.rectangle([start,start],{color:'#f7941d',weight:1,interactive:false}).addTo(m);},true);
 document.addEventListener('mousemove',e=>{if(start)rect.setBounds(L.latLngBounds(start,m.mouseEventToLatLng(e)));});
 document.addEventListener('mouseup',e=>{
  if(!start)return;const b=L.latLngBounds(start,m.mouseEventToLatLng(e));start=null;m.dragging.enable();if(rect){m.removeLayer(rect);rect=null;}
  justBoxed=true;setTimeout(()=>{justBoxed=false;},80);
  if(b.getNorthEast().equals(b.getSouthWest()))return;
  const inBox=f=>vertices(f).some(c=>b.contains([c[1],c[0]]));
  const trails=$('selTrails').checked?ctx.active.filter(inBox):[],segs=$('selRoads').checked?visibleSegments().filter(inBox):[];
  if(!trails.length&&!segs.length)return;
  for(const f of trails)sel.set(f.properties.id,f);for(const f of segs)roadSel.set(f.properties.segment_id,f);range=null;pick=0;pickPoints=[];renderSelection();
 });
}

const MODE_trailInfo=(c,p)=>window.MODE.trailInfo(c,p);
window.MODE={
 async load(){
  manifest=await (await fetch('/data/versions.json',{cache:'no-store'})).json();
  const requested=new URLSearchParams(location.search).get('version')||manifest.latest;
  const version=manifest.versions.find(v=>v.date===requested)||manifest.versions.find(v=>v.date===manifest.latest);
  DATA=await (await fetch('/'+version.url,{cache:'no-store'})).json();
  if(DATA.schema_version!==1)throw new Error('Unsupported map data format');
  const select=$('version');manifest.versions.slice().reverse().forEach(v=>select.add(new Option(v.date,v.date)));select.value=version.date;
  select.onchange=()=>{const u=new URL(location.href);u.searchParams.set('version',select.value);location.href=u.href;};
  return DATA;
 },
 trailInfo(c,p){
  const {esc,fmt,surfaces,statusLabels}=c;
  const rides=(p.rides||[]).map(id=>{const r=DATA.rides.features.find(f=>f.properties.id===id)?.properties;return `<a href="https://www.strava.com/activities/${id}" target="_blank" rel="noopener">${r?.date||id}</a>`}).join(' · ');
  return `<b>${esc(p.name)}</b><br>${(p.states||[p.state]).join(' / ')} · ${fmt(p.km)} mi · ${surfaces[p.surface]}${p.surface_detail?' ('+esc(c.vlabel('surface',p.surface_detail))+')':''}<br><strong>Matched to rides: ≈ ${fmt(p.ridden_km)} mi (${Math.round(p.coverage*100)}%)</strong><br>${statusLabels[p.status]}<br><a href="${esc(p.url)}" target="_blank" rel="noopener">Source: ${esc(p.source)} ↗</a>${rides?'<br>Rides: '+rides:''}`;
 },
 onTrailClick,
 onItemClick,
 onTrailPicked(c){if($('trail').value)setSelection(c.active,false);},
 drawOverlay(c,{matches,gps,matched}){
  const rides=rideFeatures();
  if(gps){c.add(rides,{color:'#fff',weight:7,opacity:.95},'gpshalo');c.add(visibleSegments(),{color:'#cc1667',weight:5,opacity:.9,dashArray:'5 5'},'gpslines');c.add(visibleSegments(),{color:'#000',weight:16,opacity:0},'gpslines',(f,path)=>path.on('click',e=>onSegmentClick(f,e.originalEvent)));}
  c.add((DATA.overlays?.features||[]).filter(f=>f.properties.item&&!/Polygon/.test(f.geometry.type)),{color:'#7a1fa2',weight:8,opacity:.35},'roaditems',(f,path)=>path.on('click',e=>onItemClick(f.properties.item,e.originalEvent)));
  if(matched){c.add(matches,{color:'#fff',weight:10,opacity:1},'matchhalo');c.add(matches,{color:'#cc1667',weight:6,opacity:1},'matchlines');}
 },
 stats(c){const rides=rideFeatures();return `${rides.length} rides · ${c.fmt(rides.reduce((s,f)=>s+f.properties.km,0))} mi of visible GPS · six-state catalog`;},
 fitRides(){return rideFeatures();},
 onState(){updateRides();},
 init(c){
  ctx=c;DATA.catalog.features.forEach(f=>byId.set(f.properties.id,f));
  c.map.createPane('roaditems').style.zIndex=435;
  ExcludeTool.init(c,DATA);
  updateRides();buildControls();setupBox();
  $('ride').onchange=()=>{$('gps').checked=true;c.draw();c.fitTo(rideFeatures());};
  $('selAll').onclick=()=>{sel.clear();roadSel.clear();itemSel.clear();range=null;pick=0;pickPoints=[];if($('selTrails').checked)for(const f of c.active)sel.set(f.properties.id,f);if($('selRoads').checked)for(const f of visibleSegments())roadSel.set(f.properties.segment_id,f);renderSelection();};
  $('selClear').onclick=clearSelection;
  $('pickRange').onclick=()=>{if(range){range=null;renderSelection();return;}pick=1;pickPoints=[];renderSelection();};
  $('applyEdit').onclick=apply;$('reloadAnn').onclick=loadAnnotations;
  c.map.on('click',e=>{if(lineDraw){addLinePoint(e.latlng);return;}if(ExcludeTool.handleClick(e.latlng))return;if(justBoxed)return;if(pick){handlePick(e.latlng);return;}if(sel.size)clearSelection();});
  $('drawBlocked').onclick=()=>lineDraw?(lineTool==='area'?finishLine():(cancelLine(),startLine('area'))):startLine('area');
  $('alongBtn').onclick=()=>lineDraw?(lineTool==='corridor'?finishLine():(cancelLine(),startLine('corridor'))):startLine('corridor');
  c.map.on('dblclick',()=>{if(lineDraw){lineDraw.pop();finishLine();}});
  for(const b of document.querySelectorAll('.exShape'))b.addEventListener('click',()=>cancelLine(),true);
  document.addEventListener('keydown',e=>{if(lineDraw){if(e.key==='Enter'){e.preventDefault();finishLine();}else if(e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();cancelLine();}return;}if(e.key==='Escape'&&!/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName)){e.preventDefault();clearSelection();}});
 },
 ready(){renderSelection();loadAnnotations();}
};
})();
