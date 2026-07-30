import traceback
try:
    from kaleido.scopes.plotly import PlotlyScope
    print('ok', PlotlyScope)
except Exception as e:
    print(type(e).__name__, e)
    traceback.print_exc()
