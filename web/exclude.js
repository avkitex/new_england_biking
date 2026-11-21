// Editor-only "remove from map" tool. Draws a shape, previews what it would remove, then sends the
// rule to the local server, which stores it in the PRIVATE exclusions.csv. Nothing here is published.
window.ExcludeTool=(()=>{
const $=id=>document.getElementById(id);
let ctx,DATA,token='',kind=null,pts=[],pending=null,layer=null,first=null;
const R=6371008.8,rad=d=>d*Math.PI/180;
const dist=(a,b)=>{const dl=rad(b[1]-a[1]),dn=rad(b[0]-a[0]);const h=Math.sin(dl/2)**2+Math.cos(rad(a[1]))*Math.cos(rad(b[1]))*Math.sin(dn/2)**2;return 2*R*Math.asin(Math.sqrt(h));};
const inPoly=(p,poly)=>{let c=false;for(let i=0,j=poly.length-1;i<poly.length;j=i++){const a=poly[i],b=poly[j];if((a[1]>p[1])!==(b[1]>p[1])&&p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0])c=!c;}return c;};
const status=t=>{$('exStatus').textContent=t;};
const inside=rule=>{
 if(rule.kind==='circle'){const c=[rule.lon,rule.lat];return p=>dist(c,p)<=rule.radius_m;}
 if(rule.kind==='box')return p=>p[0]>=rule.west&&p[0]<=rule.east&&p[1]>=rule.south&&p[1]<=rule.north;
 return p=>inPoly(p,rule.points);
};
function impact(rule){
 const f=inside(rule);const rides=new Set();let m=0;
 for(const r of DATA.rides.features){const lines=r.geometry.type==='LineString'?[r.geometry.coordinates]:r.geometry.coordinates;let hit=false;
  for(const l of lines)for(let i=0;i<l.length;i++){if(f(l[i])){hit=true;if(i&&f(l[i-1]))m+=dist(l[i-1],l[i]);}}
  if(hit)rides.add(r.properties.id);}
 return {rides:rides.size,miles:m/1609.344};
}
function draw(){
 if(layer){ctx.map.removeLayer(layer);layer=null;}
 const o={pane:'selected',color:'#c62d2d',weight:2,fillOpacity:.25,interactive:false};
 const g=[];
 if(pending){const r=pending;
  if(r.kind==='circle')g.push(L.circle([r.lat,r.lon],{...o,radius:r.radius_m}));
  else if(r.kind==='box')g.push(L.rectangle([[r.south,r.west],[r.north,r.east]],o));
  else g.push(L.polygon(r.points.map(p=>[p[1],p[0]]),o));}
 else if(pts.length){
  if(kind==='polygon')g.push(L.polyline(pts.map(p=>[p[1],p[0]]),{...o,dashArray:'4 4'}));
  for(const p of pts)g.push(L.circleMarker([p[1],p[0]],{...o,radius:5,fillOpacity:1}));}
 if(g.length)layer=L.layerGroup(g).addTo(ctx.map);
}
function render(){$('exConfirm').disabled=!pending||!token;$('exCancel').disabled=!pending&&!kind;}
function start(k){
 cancel(true);kind=k;pts=[];pending=null;$('map').classList.add('drawing');
 for(const b of document.querySelectorAll('.exShape'))b.classList.toggle('on',b.dataset.k===k);
 status(k==='circle'?'Click the centre, then a point on the edge.':k==='box'?'Click two opposite corners.':'Click each corner; double-click or press Enter to close.');
 $('exFinish').style.display=k==='polygon'?'':'none';render();
}
function propose(rule){
 if(rule.kind==='circle'&&rule.radius_m>20000){status('That circle is larger than 20 km. Zoom in and draw a smaller one.');cancel(true);render();return;}
 pending=rule;kind=null;$('map').classList.remove('drawing');for(const b of document.querySelectorAll('.exShape'))b.classList.remove('on');$('exFinish').style.display='none';draw();
 const i=impact(rule);
 status(`Would remove GPS inside this shape: ${i.rides} ride${i.rides===1?'':'s'}, about ${i.miles.toFixed(1)} mi. Catalog trails are not affected. Confirm to save the rule privately.`);
 render();
}
function finishPoly(){if(pts.length>=3){const points=pts.slice();pts=[];propose({kind:'polygon',points});}else status('A polygon needs at least 3 corners.');}
function click(latlng){
 const p=[latlng.lng,latlng.lat];pts.push(p);
 if(kind==='circle'&&pts.length===2){const r=Math.max(5,Math.round(dist(first,p)));pts=[];propose({kind:'circle',lat:+first[1].toFixed(6),lon:+first[0].toFixed(6),radius_m:r});}
 else if(kind==='box'&&pts.length===2){const [a,b]=pts;pts=[];propose({kind:'box',west:Math.min(a[0],b[0]),east:Math.max(a[0],b[0]),south:Math.min(a[1],b[1]),north:Math.max(a[1],b[1])});}
 else draw();
}
function cancel(keep){kind=null;pts=[];$('map').classList.remove('drawing');
 for(const b of document.querySelectorAll('.exShape'))b.classList.remove('on');$('exFinish').style.display='none';
 if(!keep){pending=null;if(layer){ctx.map.removeLayer(layer);layer=null;}status('');render();}}
async function api(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Notes-Token':token},body:JSON.stringify(body||{})});const d=await r.json();if(!r.ok)throw new Error(d.error||'Request failed');return d;}
async function confirmRule(){
 if(!pending||!token)return;$('exConfirm').disabled=true;status('Saving the rule…');
 try{const d=await api('/api/exclusions',pending);pending=null;draw();$('exRebuild').disabled=false;status(`Rule saved privately (${d.rules} in total). Rebuild to apply it: the map still shows the old data until then.`);}
 catch(e){status('Not saved: '+e.message);}
 render();
}
async function rebuild(){
 $('exRebuild').disabled=true;status('Rebuilding all snapshots… this can take a minute or two.');
 try{await api('/api/rebuild');status('Rebuilt. Reloading…');location.reload();}catch(e){status('Rebuild failed: '+e.message);$('exRebuild').disabled=false;}
}
return {
 active:()=>!!kind,
 setToken:t=>{token=t;render();},
 handleClick(latlng){if(!kind)return false;if(!pts.length)first=[latlng.lng,latlng.lat];click(latlng);return true;},
 init(c,data){ctx=c;DATA=data;
  for(const b of document.querySelectorAll('.exShape'))b.onclick=()=>start(b.dataset.k);
  $('exFinish').onclick=finishPoly;$('exConfirm').onclick=confirmRule;$('exCancel').onclick=()=>cancel(false);$('exRebuild').onclick=rebuild;
  c.map.doubleClickZoom.disable();
  c.map.on('dblclick',()=>{if(kind==='polygon'){pts.pop();finishPoly();}});
  document.addEventListener('keydown',e=>{if(!kind)return;if(e.key==='Enter'&&kind==='polygon'){e.preventDefault();finishPoly();}if(e.key==='Escape'){e.stopImmediatePropagation();cancel(false);}},true);
  render();}
};
})();
