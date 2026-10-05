<#
! @brief 通过已登录飞书页面的原生图片菜单上传文档配图。
! @note 只修改指定占位块并调用页面既有上传处理器，不构造飞书接口。
#>
param(
    [Parameter(Mandatory = $true)][int]$BlockId,
    [Parameter(Mandatory = $true)][string]$ImagePath,
    [Parameter(Mandatory = $true)][string]$Caption
)
$ErrorActionPreference = 'Stop'
$taskBrowser = 'C:\Users\23201\AppData\Roaming\npm\node_modules\agent-browser\bin\agent-browser-win32-x64.exe'

function Invoke-PageScript([string]$Script) {
    $taskAnswer = $Script | & $taskBrowser --auto-connect eval --stdin --json | ConvertFrom-Json
    if (!$taskAnswer.success) { throw $taskAnswer.error }
    return $taskAnswer.data.result
}

$taskArgs = @{ block = $BlockId; caption = $Caption; name = [IO.Path]::GetFileName($ImagePath) } | ConvertTo-Json -Compress
$taskPrepare = @'
(async()=>{
    const p=PARAMS,b=PageMain.editor.blockManager,m=b.getBlockModelByBlockId(p.block);
    if(!b.editable)throw new Error('文档无编辑权限');
    const previous=m.getText().replace(/\n$/,'');
    if(!previous.includes('配图位')&&previous!==p.caption)throw new Error('目标不是图片占位或已确认图注');
    if(previous!==p.caption)await m.replaceText([0,previous.length],{text:p.caption});
    const next=m.nextSibling;
    if(next?.type==='image'){
        if(next.snapshot.image.name!==p.name)throw new Error('图注后已有不同图片，禁止重复插入');
        return {already:true,id:next.id,recordId:next.recordId,name:next.snapshot.image.name,token:next.snapshot.image.token,width:next.snapshot.image.width,height:next.snapshot.image.height,parent:next.parent.id};
    }
    window.__dpUploadBefore=new Set(b.allBlockModels.filter(x=>x.type==='image').map(x=>x.id));
    PageMain.scroller.scrollTop=b.layoutManager.getLayoutInfo(p.block).top-130;
    await new Promise(r=>setTimeout(r,250));
    m.setSelection({start:m.getText().length-1,len:0},true);m.editor.selection.focus();
    document.querySelector('#codex-doc-image-upload')?.removeAttribute('id');
    window.__dpRestoreFileClick?.();
    const original=HTMLInputElement.prototype.click;
    window.__dpRestoreFileClick=()=>{HTMLInputElement.prototype.click=original};
    HTMLInputElement.prototype.click=function(){
        if(this.type==='file'){
            this.id='codex-doc-image-upload';
            if(!this.isConnected){this.style.display='none';document.body.appendChild(this);}
            HTMLInputElement.prototype.click=original;
        }
        return original.apply(this,arguments);
    };
    return {block:p.block,caption:p.caption};
})()
'@
$taskPrepared = Invoke-PageScript $taskPrepare.Replace('PARAMS', $taskArgs)
if ($taskPrepared.already) {
    $taskPrepared | Add-Member -NotePropertyName caption -NotePropertyValue $Caption
    $taskPrepared | Add-Member -NotePropertyName placeholder -NotePropertyValue $BlockId
    $taskPrepared | Add-Member -NotePropertyName localFile -NotePropertyValue $ImagePath
    $taskPrepared | ConvertTo-Json -Depth 4
    exit 0
}
& $taskBrowser --auto-connect press Enter | Out-Null
& $taskBrowser --auto-connect keyboard type '/' | Out-Null

$taskOpen = @'
(async()=>{
    let item;
    for(let i=0;i<60;i++){
        item=Array.from(document.querySelectorAll('.menu-text')).find(e=>e.textContent==='图片');
        if(item)break;
        await new Promise(r=>setTimeout(r,50));
    }
    if(!item)throw new Error('原生图片菜单未打开');
    item.click();
    for(let i=0;i<60;i++){
        if(document.querySelector('#codex-doc-image-upload'))return {ready:true};
        await new Promise(r=>setTimeout(r,50));
    }
    window.__dpRestoreFileClick?.();throw new Error('原生上传框未出现');
})()
'@
Invoke-PageScript $taskOpen | Out-Null
& $taskBrowser --auto-connect upload '#codex-doc-image-upload' $ImagePath | Out-Null
if ($LASTEXITCODE -ne 0) { throw '原生图片上传失败' }

$taskVerify = @'
(async()=>{
    window.__dpRestoreFileClick?.();
    const b=PageMain.editor.blockManager;
    for(let i=0;i<80;i++){
        const added=b.allBlockModels.filter(m=>m.type==='image'&&!window.__dpUploadBefore.has(m.id));
        if(added.length>1)throw new Error('一次上传产生多张图片');
        if(added.length===1&&added[0].snapshot.image.token){
            const m=added[0];return {id:m.id,recordId:m.recordId,name:m.snapshot.image.name,token:m.snapshot.image.token,width:m.snapshot.image.width,height:m.snapshot.image.height,parent:m.parent.id};
        }
        await new Promise(r=>setTimeout(r,250));
    }
    throw new Error('上传后未获得服务器图片标识');
})()
'@
$taskInserted = Invoke-PageScript $taskVerify
$taskInserted | Add-Member -NotePropertyName caption -NotePropertyValue $Caption
$taskInserted | Add-Member -NotePropertyName placeholder -NotePropertyValue $BlockId
$taskInserted | Add-Member -NotePropertyName localFile -NotePropertyValue $ImagePath
$taskInserted | ConvertTo-Json -Depth 4
