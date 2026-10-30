/* Independent reader for Hangar Export's documented CSV columns and item tags. */
(() => {
  function rows(text) {
    if(text.length>5*1024*1024)throw Error('CSV is too large (maximum 5 MB).');
    text=text.replace(/^\uFEFF/,'');
    const result=[];let row=[],cell='',quoted=false,ended=false;
    const finish=()=>{row.push(cell);cell='';ended=false;};
    for(let i=0;i<text.length;i++){
      const c=text[i];
      if(quoted){if(c==='"'){if(text[i+1]==='"'){cell+='"';i++;}else{quoted=false;ended=true;}}else cell+=c;continue;}
      if(c===','){finish();continue;}
      if(c==='\r'||c==='\n'){
        if(c==='\r'&&text[i+1]==='\n')i++;
        finish();if(row.some(v=>v.trim()))result.push(row);row=[];
        if(result.length>5001)throw Error('CSV has too many rows.');
      }else if(c==='"'&&!cell&&!ended)quoted=true;
      else if(ended||c==='"')throw Error('Malformed CSV quoting. Export the file again without editing its columns.');
      else cell+=c;
    }
    if(quoted)throw Error('CSV ends inside a quoted field.');
    finish();if(row.some(v=>v.trim()))result.push(row);
    return result;
  }
  async function parse(text) {
    const data=rows(text),header=data.shift()?.map(h=>h.trim().toLowerCase());
    const required=['pledgeid','name','items','alsocontains'];
    if(!header||!/(?:^|\s)type$/.test(header[0])||required.some(h=>!header.includes(h))||new Set(header).size!==header.length)
      throw Error('Expected a HangarLink Hangar Export CSV with Type, PledgeId, Name, Items and AlsoContains columns.');
    const items=[],packages=[],unsupported=[],seen=new Set();
    for(const [index,row] of data.entries()){
      if(row.length!==header.length)throw Error(`CSV row ${index+2} has the wrong number of columns. No changes applied.`);
      const get=name=>row[header.indexOf(name)]?.trim()||'';
      const title=get('name'),type=row[0].trim().toUpperCase();
      if(type!=='PLEDGE'){unsupported.push(`Row ${index+2}: ${type||'unknown type'} is not owned inventory`);continue;}
      const key=get('pledgeid');
      if(key&&seen.has(key))throw Error('CSV contains a repeated PledgeId. No changes applied.');
      if(key)seen.add(key);
      const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(key||`${index}:${title}`));
      const packageId='hangarlink:'+Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('').slice(0,32);
      const insurance=[...get('alsocontains').matchAll(/\b(Lifetime Insurance|LTI|\d+\s+(?:Month|Year)s?\s+Insurance)\b/gi)].map(m=>({label:m[1]}));
      const upgrade=title.match(/^(?:Upgrade|Ship Upgrades?)\s*-\s*(.+?)\s+to\s+(.+)$/i);
      let found=[];
      if(upgrade){
        const source=upgrade[1].trim(),target=upgrade[2].replace(/\s*-?\s*Warbond(?: Edition)?$/i,'').trim();
        if(source&&target&&source.length<=100&&target.length<=100)found=[{kind:'ccu',source_ship:source,target_ship:target}];
      }else{
        for(const match of get('items').matchAll(/\[KIND\s+([^\]]+)\]\[TITLE\s+([^\]]+)\]/gi)){
          if(/^(ship|vehicle)$/i.test(match[1].trim())&&match[2].trim()&&match[2].trim().length<=100)
            found.push({kind:'ship',name:match[2].trim(),upgrade_base_eligible:true});
        }
      }
      if(!found.length){unsupported.push(`Row ${index+2}: ${title.slice(0,150)||'unnamed pledge'} has no supported ship or unambiguous CCU`);continue;}
      packages.push({id:packageId,title:title.slice(0,150)});
      found.forEach((item,i)=>items.push({...item,id:`${packageId}:${i}`,package_id:packageId,identity:'uncertain',ownership:'owned',insurance}));
      if(items.length>600)throw Error('Import exceeds the planner limit of 600 observations.');
    }
    if(!items.length)throw Error('No supported owned ships or CCUs found. Buybacks and non-ship items are not imported.');
    return {schema_version:1,source:'HangarLink CSV',items,packages,sync:{complete:false},unsupported};
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={parse};
  else window.HangarLinkCSV={parse};
})();
