import json, os, zlib, struct, base64, urllib.request
def png(w,h,rgb):
    raw=b"".join(b"\x00"+bytes(rgb)*w for _ in range(h))
    def ch(t,d): c=struct.pack(">I",len(d))+t+d; return c+struct.pack(">I",zlib.crc32(t+d)&0xffffffff)
    return b"\x89PNG\r\n\x1a\n"+ch(b"IHDR",struct.pack(">IIBBBBB",w,h,8,2,0,0,0))+ch(b"IDAT",zlib.compress(raw))+ch(b"IEND",b"")
KEY=open(os.path.expanduser("~/spark-llm/api.key")).read().strip()
for color,rgb in (("red",(220,20,20)),("blue",(20,40,220))):
    uri="data:image/png;base64,"+base64.b64encode(png(128,128,rgb)).decode()
    b={"model":"qwen3.8-27b","max_tokens":300,"messages":[{"role":"user","content":[{"type":"text","text":"What is the single dominant color of this image? Answer with one word."},{"type":"image_url","image_url":{"url":uri}}]}]}
    r=json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8080/v1/chat/completions",json.dumps(b).encode(),{"Authorization":f"Bearer {KEY}","Content-Type":"application/json"}),timeout=300))
    m=r["choices"][0]["message"]; print("truth",color,"->",repr((m.get("content") or "")[:40]))
