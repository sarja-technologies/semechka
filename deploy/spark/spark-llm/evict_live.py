import json, os, threading, time, urllib.request
URL="http://127.0.0.1:8080"; KEY=open(os.path.expanduser("~/spark-llm/api.key")).read().strip()
H={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"}
def loaded():
    r=json.load(urllib.request.urlopen(urllib.request.Request(URL+"/v1/models",headers=H),timeout=30))["data"]
    return sorted(m["id"] for m in r if (m["status"]["value"] if isinstance(m.get("status"),dict) else m.get("status"))=="loaded")
out={}
def s(tag,model,prompt,n):
    t0=time.time(); got=0; err=None; fin=None
    try:
        b={"model":model,"stream":True,"max_tokens":n,"messages":[{"role":"user","content":prompt}]}
        with urllib.request.urlopen(urllib.request.Request(URL+"/v1/chat/completions",json.dumps(b).encode(),H),timeout=900) as r:
            for raw in r:
                l=raw.decode().strip()
                if l.startswith("data:") and not l.endswith("[DONE]"):
                    j=json.loads(l[5:])
                    for ch in j.get("choices",[]):
                        d=ch.get("delta",{}); got+=1 if (d.get("content") or d.get("reasoning_content")) else 0; fin=ch.get("finish_reason") or fin
    except Exception as e: err=str(e)[:100]
    out[tag]=dict(model=model,chunks=got,finish=fin,secs=round(time.time()-t0,1),err=err)
print("loaded:",loaded())
long=threading.Thread(target=s,args=("LONG","qwen3.8-27b","Write a very long detailed essay on the history of mathematics, at least 700 words.",900)); long.start()
time.sleep(4)
for m in ("qwen3.6-35b-a3b","qwen3.5-0.8b"):  # refresh the other two so the streaming 27B becomes the least recently used
    s("touch-"+m,m,"Say ok.",8)
print("after touches loaded:",loaded())
s("TRIGGER","qwen3-4b-instruct-2507","Say hi.",20)   # 4th model -> must evict somebody
print("after trigger loaded:",loaded())
long.join()
for k,v in out.items(): print(k,v)
