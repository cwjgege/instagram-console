"""Shared desktop presentation: restrained color, native controls, readable forms."""
import tkinter as tk
from tkinter import ttk

BG='#F3F5F7';SURFACE='#FFFFFF';INK='#182329';MUTED='#54636E';BLUE='#185ADB';LINE='#D8DFE5'
GREEN='#176948';RED='#A52D38';AMBER='#825B12'

def apply(root):
    style=ttk.Style(root)
    theme='aqua' if 'aqua' in style.theme_names() else 'clam'
    if style.theme_use()!=theme:style.theme_use(theme)
    root.configure(background=BG);root.option_add('*Font',('PingFang SC',12))
    style.configure('.',font=('PingFang SC',12),background=SURFACE,foreground=INK)
    style.configure('TFrame',background=SURFACE)
    style.configure('Workspace.TFrame',background=BG)
    style.configure('TLabel',background=SURFACE,foreground=INK)
    style.configure('Muted.TLabel',foreground=MUTED,font=('PingFang SC',11))
    style.configure('Workspace.TLabel',background=BG,foreground=MUTED)
    style.configure('Title.TLabel',font=('PingFang SC',18,'bold'))
    style.configure('Section.TLabel',font=('PingFang SC',14,'bold'),foreground=INK,background=SURFACE)
    style.configure('Status.TLabel',font=('PingFang SC',13,'bold'))
    style.configure('TButton',padding=(10,4),relief='flat',borderwidth=1,background='#F6F8FA',bordercolor=LINE)
    style.map('TButton',background=[('pressed','#DCE5EF'),('active','#EAF0F5')],foreground=[('disabled','#7A858E')])
    style.configure('Primary.TButton',background=BLUE,foreground=BLUE if theme=='aqua' else 'white',bordercolor=BLUE,padding=(12,5))
    style.map('Primary.TButton',background=[('disabled','#E4E9EE'),('pressed','#1042A8'),('active','#124DC2')],foreground=[('disabled','#65727D'),('!disabled',BLUE if theme=='aqua' else 'white')])
    style.configure('Action.TButton',padding=(14,8))
    style.configure('TEntry',padding=6,fieldbackground=SURFACE,bordercolor=LINE)
    style.configure('TCombobox',padding=5,fieldbackground=SURFACE,bordercolor=LINE)
    style.configure('TSpinbox',padding=5,fieldbackground=SURFACE,bordercolor=LINE,arrowsize=14)
    style.configure('TCheckbutton',padding=(0,4),background=SURFACE)
    style.configure('TNotebook',background=SURFACE,borderwidth=0,tabmargins=(0,4,0,0))
    style.configure('TNotebook.Tab',padding=(11,8),background=BG,bordercolor=LINE)
    style.map('TNotebook.Tab',background=[('selected',SURFACE)],foreground=[('selected',BLUE)])
    style.configure('Fleet.Treeview',rowheight=38,font=('PingFang SC',12),background=SURFACE,fieldbackground=SURFACE,borderwidth=0)
    style.configure('Fleet.Treeview.Heading',font=('PingFang SC',11,'bold'),background=BG,foreground=MUTED,padding=(10,9),relief='flat')
    style.map('Fleet.Treeview',background=[('selected','#E9F0FE')],foreground=[('selected','#173A70')])
    style.configure('Horizontal.TProgressbar',background=BLUE,troughcolor=BG,borderwidth=0,thickness=3)
    return style

def scroll_form(parent,padding=16):
    """A form that can shrink while keeping its own wheel events scoped."""
    area=ttk.Frame(parent);area.pack(fill='both',expand=True)
    canvas=tk.Canvas(area,background=SURFACE,highlightthickness=1,highlightbackground=SURFACE,highlightcolor=BLUE,borderwidth=0,takefocus=True)
    scrollbar=ttk.Scrollbar(area,orient='vertical',command=canvas.yview)
    scrollbar.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
    canvas.configure(yscrollcommand=scrollbar.set)
    def page(amount):canvas.yview_scroll(amount,'pages');return 'break'
    canvas.bind('<Next>',lambda _:page(1));canvas.bind('<Prior>',lambda _:page(-1))
    canvas.bind('<Down>',lambda _:(canvas.yview_scroll(30,'units'),'break')[-1])
    canvas.bind('<Up>',lambda _:(canvas.yview_scroll(-30,'units'),'break')[-1])
    body=ttk.Frame(canvas,padding=padding);window=canvas.create_window((0,0),window=body,anchor='nw')
    body.bind('<Configure>',lambda _:canvas.configure(scrollregion=canvas.bbox('all')))
    def resize(event):
        canvas.itemconfigure(window,width=event.width)
        for widget in body.winfo_children():
            if isinstance(widget,ttk.Label):widget.configure(wraplength=max(220,event.width-padding*2))
    canvas.bind('<Configure>',resize)
    bind_form_scroll(parent.winfo_toplevel(),canvas,body)
    body.canvas=canvas
    return body


def bind_form_scroll(root,canvas,body):
    """Handle Tk9 precise trackpad events as well as conventional mouse wheels."""
    canvas.configure(yscrollincrement=1)
    def belongs(widget):
        while widget is not None:
            if widget in (body,canvas):return True
            widget=getattr(widget,'master',None)
        return False
    def handle(event,precise=False):
        widget=event.widget
        if not belongs(widget):
            # Some macOS wheel events target the window instead of the hovered child.
            if widget!=root:return
            x,y=root.winfo_pointerxy();widget=root.winfo_containing(x,y)
            if not belongs(widget):return
        if widget.winfo_class() in ('Text','Listbox','TSpinbox','TCombobox'):return
        if precise:
            _,dy=canvas.tk.call('tk::PreciseScrollDeltas',event.delta)
            amount=-int(dy)
        else:
            delta=getattr(event,'delta',0)
            if getattr(event,'num',None) in (4,5):amount=-40 if event.num==4 else 40
            elif not delta:return
            elif canvas.tk.call('tk','windowingsystem')=='aqua':amount=-int(delta)*8
            else:amount=-round(delta/120*40)
        if amount and canvas.yview()!=(0.0,1.0):canvas.yview_scroll(amount,'units')
        return 'break'
    bindings=[]
    for sequence,callback in [('<MouseWheel>',handle),('<TouchpadScroll>',lambda e:handle(e,True)),('<Button-4>',handle),('<Button-5>',handle)]:
        try:bindings.append((sequence,root.bind(sequence,callback,add='+')))
        except tk.TclError:pass  # Tk8 does not expose TouchpadScroll.
    def cleanup(event):
        if event.widget!=canvas:return
        for sequence,token in bindings:
            try:root.unbind(sequence,token)
            except tk.TclError:pass
    canvas.bind('<Destroy>',cleanup,add='+')
    canvas.form_scroll=handle
