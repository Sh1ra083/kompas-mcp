"""Render only requested source pages, once per content hash. Requires pypdfium2/Pillow."""
import argparse,hashlib,json,pathlib

def prepare(source,output,pages,scale):
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    output.mkdir(parents=True,exist_ok=True)
    folder=output/(digest[:20]+'-scale-'+str(scale));folder.mkdir(exist_ok=True)
    files=[];cached=0
    if source.suffix.lower()=='.pdf':
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(str(source)) as doc:
            count=len(doc)
            if not pages:
                return {'source_sha256':digest,'page_count':count,'action':'Choose relevant 1-based pages with --pages; no pages rendered.'}
            if min(pages)<1 or max(pages)>count:raise ValueError('Page outside document')
            for number in sorted(set(pages)):
                path=folder/f'page-{number}.png'
                if path.exists():cached+=1
                else:
                    page=doc[number-1];bitmap=page.render(scale=scale)
                    bitmap.to_pil().save(path);bitmap.close();page.close()
                files.append(str(path))
    else:
        from PIL import Image
        count=1;path=folder/'page-1.png'
        if path.exists():cached=1
        else:
            with Image.open(source) as im:im.convert('RGB').save(path)
        files.append(str(path))
    result={'source':str(source),'source_sha256':digest,'page_count':count,'images':files,'cached_pages':cached,
            'next':'Inspect images once; record dimensions, units, assumptions, feature plan and acceptance checks in brief.json. Do not infer missing dimensions silently.'}
    (folder/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=pathlib.Path);p.add_argument('--output',type=pathlib.Path,required=True);p.add_argument('--pages',type=int,nargs='+');p.add_argument('--scale',type=float,default=2.5);a=p.parse_args()
    if not 0.5<=a.scale<=6:p.error('scale must be between 0.5 and 6')
    print(json.dumps(prepare(a.source.resolve(),a.output.resolve(),a.pages,a.scale),ensure_ascii=True))
