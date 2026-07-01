const fs = require('fs');
const path = require('path');
const lines = fs.readFileSync(path.join(__dirname,'LaoNiu.body.txt'),'utf8').split('\n');
const markers = ['以上是过往记忆区','VCP元思维','VCP元思考','日记本','时间锚点','工具箱','工具列表','VarToolList','VCPCommunication','VCPFileTool','ToolBox','下面列出','可用工具','# ','## '];
lines.forEach((l,i)=>{
  for(const m of markers){
    if(l.includes(m)){ console.log(`${i+1}: ${l.slice(0,90)}`); break; }
  }
});
console.log('TOTAL LINES:', lines.length);
