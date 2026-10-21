from kompas_mcp.layout import pack

def row(n,box):return {'view_number':n,'bounds_mm':box,'origin':[0,0]}

def test_pack_accounts_for_gaps_and_full_annotation_box():
    rows=[row(1,[-20,-10,60,80]),row(2,[-10,-10,50,50])]
    result=pack(rows,[23,63,202,275],3)
    assert result and len(result)==2
    placed=[]
    for r,p in zip(rows,result):
        x,y=p['origin'];a,b,c,d=r['bounds_mm'];box=[a+x,b+y,c+x,d+y]
        assert box[0]>=23 and box[1]>=63 and box[2]<=202 and box[3]<=275
        assert 'detach_projection_alignment' not in p
        placed.append(box)
    assert placed[1][3]+3<=placed[0][1]

def test_oversized_annotation_bounds_do_not_fit():
    assert pack([row(1,[0,0,250,10])],[23,63,202,275],3) is None
