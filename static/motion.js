'use strict';
/* =========================================================
   ノッタ v3.29 画面運動（スクロール駆動・落款・読了位置）
   - 外部通信なし・依存追加なし（lottie-web は同梱のライト版のみ）
   - 閲覧者は window.nottaMotion.attach() で再描画ごとに再接続する
   ========================================================= */
(function(){
  const reduce=window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let observer=null, ticking=false;

  /* --- スクロール連動：読み込み位置（reveal） --- */
  function reveal(){
    const root=document.getElementById('main');
    if(!root)return;
    const nodes=[...root.children];
    if(!nodes.length)return;
    if(reduce||!('IntersectionObserver' in window)){
      nodes.forEach(n=>n.classList.add('is-visible'));
      return;
    }
    if(observer)observer.disconnect();
    observer=new IntersectionObserver(entries=>{
      entries.forEach(entry=>{
        if(entry.isIntersecting){
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      });
    },{root:null,rootMargin:'0px 0px -6% 0px',threshold:0.02});
    nodes.forEach(n=>{
      n.classList.add('reveal');
      const rect=n.getBoundingClientRect();
      if(rect.top<window.innerHeight*0.92){
        requestAnimationFrame(()=>n.classList.add('is-visible'));
      }else{
        observer.observe(n);
      }
    });
    /* 保険：画面内にいる要素は900msまでに必ず見せる（観測が効かない環境向け） */
    setTimeout(()=>{
      nodes.forEach(n=>{
        if(n.getBoundingClientRect().top<window.innerHeight*0.92)n.classList.add('is-visible');
      });
    },900);
  }

  /* --- スクロール連動：読了位置（ヘッダーの細い線） --- */
  function progress(){
    const rail=document.querySelector('.scroll-rail i');
    if(!rail)return;
    const doc=document.documentElement;
    const total=Math.max(doc.scrollHeight,document.body?document.body.scrollHeight:0)-window.innerHeight;
    const p=total>8?Math.min(1,Math.max(0,window.scrollY/total)):0;
    rail.style.setProperty('--p',p.toFixed(4));
  }
  function onScroll(){
    if(ticking)return;
    ticking=true;
    requestAnimationFrame(()=>{progress();ticking=false;});
  }

  /* --- ナビの現在地を横スクロールで見せる（スマホ） --- */
  function currentInView(){
    const current=document.querySelector('nav button[aria-current]');
    const nav=document.querySelector('nav');
    if(!current||!nav||!current.scrollIntoView)return;
    if(nav.scrollWidth<=nav.clientWidth+4)return;
    try{current.scrollIntoView({block:'nearest',inline:'center'});}catch(e){}
  }

  /* --- 確認の落款（Lottie: 同梱seal.json + lottie-web） --- */
  let player=null;
  function ensurePlayer(){
    if(window.lottie)return Promise.resolve(true);
    if(player)return player;
    player=new Promise(resolve=>{
      const s=document.createElement('script');
      s.src='/vendor/lottie_light.min.js';
      s.onload=()=>resolve(!!window.lottie);
      s.onerror=()=>resolve(false);
      document.head.appendChild(s);
    });
    return player;
  }
  function mountSeal(card){
    const box=card.querySelector('.stamp');
    if(!box)return;
    if(!window.lottie||typeof window.lottie.loadAnimation!=='function'){box.remove();sealFallback(card);return}
    try{
      const anim=window.lottie.loadAnimation({
        container:box,renderer:'svg',loop:false,autoplay:true,path:'/assets/seal.json'
      });
      if(anim&&typeof anim.addEventListener==='function'){
        anim.addEventListener('data_failed',()=>{box.remove();sealFallback(card);});
      }
    }catch(e){box.remove();sealFallback(card);}
  }
  function stamp(){
    const cards=[...document.querySelectorAll('.card.has-stamp')].filter(c=>!c.dataset.stamped);
    if(!cards.length)return;
    cards.forEach(card=>{
      card.dataset.stamped='1';
      const box=document.createElement('div');
      box.className='stamp';
      box.setAttribute('aria-hidden','true');
      card.appendChild(box);
    });
    ensurePlayer().then(ok=>{
      cards.forEach(card=>{
        if(ok)mountSeal(card);
        else{const b=card.querySelector('.stamp');if(b)b.remove();sealFallback(card);}
      });
    });
  }
  function sealFallback(card){
    if(card.querySelector('.seal-fallback'))return;
    const el=document.createElement('span');
    el.className='seal-fallback';
    el.setAttribute('aria-hidden','true');
    el.textContent='確認済';
    card.appendChild(el);
  }

  function attach(){
    reveal();
    progress();
    currentInView();
    stamp();
  }

  window.addEventListener('scroll',onScroll,{passive:true});
  window.addEventListener('resize',onScroll,{passive:true});
  document.addEventListener('DOMContentLoaded',()=>{progress();});
  window.nottaMotion={attach:attach,progress:progress,stamp:stamp};
  if(document.readyState!=='loading')attach();
})();
