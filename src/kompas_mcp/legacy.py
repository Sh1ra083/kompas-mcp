"""Small API5 fallback for explicit-unit mass properties only."""
import pythoncom
import win32com.client as com

class LegacyMass:
    def __init__(self,bindir,constants):
        a=pythoncom.LoadTypeLib(str(bindir/'kAPI5.tlb')).GetLibAttr()
        self.api=com.gencache.EnsureModule(a[0],a[1],a[3],a[4])
        self.constants=constants
        self.app=com.Dispatch('KOMPAS.Application.5')

    def calculate(self,document7):
        rawdoc=self.app.ksGetDocumentByReference(document7.Reference)
        doc=self.api.ksDocument3D(rawdoc._oleobj_.QueryInterface(self.api.ksDocument3D.CLSID,pythoncom.IID_IDispatch))
        rawpart=doc.GetPart(self.constants['pTop_Part'])
        klass=self.api.ksPart
        part5=klass(rawpart._oleobj_.QueryInterface(klass.CLSID,pythoncom.IID_IDispatch))
        # SDK Include/ldefin2d.h: ST_MIX_MM=0x1, ST_MIX_KG=0x10 (not exported in TLB).
        raw=part5.CalcMassInertiaProperties(0x1|0x10)
        klass=self.api.ksMassInertiaParam
        result=klass(raw._oleobj_.QueryInterface(klass.CLSID,pythoncom.IID_IDispatch))
        return {'mass_kg':result.m,'volume_mm3':result.v,'area_mm2':result.F,'center_mm':[result.xc,result.yc,result.zc]}
