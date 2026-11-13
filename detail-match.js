/* Offline appearance matching for a user-selected accessory. */
const DetailMatch = (() => {
 const span=5;
 function raster(bitmap){
  const scale=Math.min(1,320/Math.max(bitmap.width,bitmap.height));
  const canvas=document.createElement('canvas');
  canvas.width=Math.max(1,Math.round(bitmap.width*scale));
  canvas.height=Math.max(1,Math.round(bitmap.height*scale));
  const ctx=canvas.getContext('2d');
  ctx.drawImage(bitmap,0,0,canvas.width,canvas.height);
  return {w:canvas.width,h:canvas.height,p:ctx.getImageData(0,0,canvas.width,canvas.height).data};
 }
 function patch(image,x,y,r){
  const out=[],mean=[0,0,0];
  if(x-r<0||y-r<0||x+r>=image.w||y+r>=image.h)return null;
  for(let j=-span;j<=span;j++)for(let i=-span;i<=span;i++){
   const offset=4*(Math.round(y+j*r/span)*image.w+Math.round(x+i*r/span));
   for(let c=0;c<3;c++){const v=image.p[offset+c];out.push(v);mean[c]+=v;}
  }
  let sum=0;
  for(let c=0;c<3;c++)mean[c]/=out.length/3;
  for(let i=0;i<out.length;i++){out[i]-=mean[i%3];sum+=out[i]*out[i];}
  const norm=Math.sqrt(sum);
  if(norm<12*Math.sqrt(out.length))return null;
  return {out:out.map(v=>v/norm),mean};
 }
 function anchor(bitmap,x,y){
  const image=raster(bitmap),r=Math.max(5,Math.round(Math.min(image.w,image.h)*.095));
  const sample=patch(image,Math.round(x*(image.w-1)),Math.round(y*(image.h-1)),r);
  return sample?{sample,fraction:r/Math.min(image.w,image.h)}:null;
 }
 function locate(bitmap,ref){
  if(!ref)return null;
  const im=raster(bitmap),hits=[],base=ref.fraction*Math.min(im.w,im.h);
  function score(x,y,r){
   const s=patch(im,x,y,r);if(!s)return -1;
   let dot=0;for(let i=0;i<s.out.length;i++)dot+=s.out[i]*ref.sample.out[i];
   const delta=Math.hypot(...s.mean.map((v,c)=>v-ref.sample.mean[c]));
   return dot-Math.min(.25,delta/600);
  }
  for(const factor of [.65,.85,1,1.25,1.55,1.9]){
   const r=Math.max(4,Math.round(base*factor)),step=Math.max(2,Math.round(r/6));
   let top=[];
   for(let y=r;y<im.h-r;y+=step)for(let x=r;x<im.w-r;x+=step){
    const value=score(x,y,r);
    if(value>.48){top.push({x,y,r,value});}
   }
   top.sort((a,b)=>b.value-a.value);
   for(const seed of top.slice(0,12)){
    let best=seed;
    for(let j=-step;j<=step;j++)for(let i=-step;i<=step;i++){
     const x=seed.x+i,y=seed.y+j,value=score(x,y,r);
     if(value>best.value)best={x,y,r,value};
    }
    hits.push(best);
   }
  }
  hits.sort((a,b)=>b.value-a.value);
  const best=hits[0];if(!best||best.value<.72)return null;
  const rival=hits.find(h=>Math.hypot(h.x-best.x,h.y-best.y)>best.r*1.5);
  if(rival&&best.value-rival.value<.07)return null;
  return {x:best.x/(im.w-1),y:best.y/(im.h-1),scale:best.r/base};
 }
 return {anchor,locate};
})();
