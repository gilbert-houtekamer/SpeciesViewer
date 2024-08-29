import flet as ft

# tutorial from flet online manual, version to create a
# blog GUI prototype, independent of Jan's version, more
# to learn how the GUI works.

# The design of the panels is like this:

# <text Title NL>        <edit>  <language>
# <other language>       <edit>

# <decription NL>        <edit>  <language>
# <other language>       <edit>

# <selection>            <edit>  <set back to biota>
# <new?>
# <min rating>

# <run>

class Task(ft.Column):
    def __init__(self, task_name, task_status_change, task_delete):
        super().__init__()
        self.completed = False
        self.task_name = task_name
        self.task_status_change = task_status_change
        self.task_delete = task_delete
        self.edit_multiline = False        
        self.display_task = ft.Checkbox(
            value=False, label=self.task_name, on_change=self.status_changed
        )
        self.edit_name = ft.TextField(expand=1, multiline=self.edit_multiline, )

        self.display_view = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                self.display_task,
                ft.Row(
                    spacing=0,
                    controls=[
                        ft.IconButton(
                            icon=ft.icons.CREATE_OUTLINED,
                            tooltip="Edit To-Do",
                            on_click=self.edit_clicked,
                        ),
                        ft.IconButton(
                            ft.icons.DELETE_OUTLINE,
                            tooltip="Delete To-Do",
                            on_click=self.delete_clicked,
                        ),
                    ],
                ),
            ],
        )

        self.edit_view = ft.Row(
            visible=False,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                self.edit_name,
                ft.IconButton(
                    icon=ft.icons.DONE_OUTLINE_OUTLINED,
                    icon_color=ft.colors.GREEN,
                    tooltip="Update To-Do",
                    on_click=self.save_clicked,
                ),
            ],
        )
        self.controls = [self.display_view, self.edit_view]

    def edit_clicked(self, e):
        self.edit_name.value = self.display_task.label
        self.display_view.visible = False
        self.edit_view.visible = True
        self.edit_multiline = True
        self.update()

    def save_clicked(self, e):
        self.display_task.label = self.edit_name.value
        self.display_view.visible = True
        self.edit_view.visible = False
        self.update()

    def status_changed(self, e):
        self.completed = self.display_task.value
        self.task_status_change()

    def delete_clicked(self, e):
        self.task_delete(self)


class TodoApp(ft.Column):
    # application's root control is a Column containing all other controls
    def __init__(self):
        super().__init__()

        self.titletext = ft.TextField(value="Make Blog Post", read_only=True, 
                                text_align=ft.TextAlign.CENTER, text_size=32,                                
                                expand=True, bgcolor="#808000", color=ft.colors.WHITE)
        self.blog_title = ft.TextField(label="Titel (Nederlands)", 
                                hint_text="Voer de titel in het Nederlands in.", 
                                on_submit=self.add_title_clicked, on_blur=self.add_lostfocus_clicked,
                                autofocus=True, 
                                multiline=False, expand=True, disabled=False)
        self.blog_title_en = ft.TextField(label="Translated Title (English)", 
                                hint_text="Translated title", 
                                autofocus=True, 
                                multiline=False, expand=True, disabled=True)
        self.blog_message = ft.TextField(label="Waar gaat de blog over?",
                                hint_text="Geef de introductie in een paar Nederlandse zinnen.", 
                                on_blur=self.add_message_clicked,
                                multiline=True, expand=True, disabled=False)
        self.blog_message_en = ft.TextField(label="What is this blog about?", \
                                hint_text="Provide an introduction to the pictures in a few sentences.",\
                                multiline=True, expand=True, disabled=True)        
        self.blog_species = ft.TextField(label='De wetenschappelijke naam om te selecteren, bij voorbeeld ' +  
                                         '"biota" (alle leven), "aves", "plantae" or "insecta".', 
                                on_blur=self.validate_species,
                                value = 'biota', autofocus=True, 
                                multiline=False, expand=True)
        self.blog_minrating = ft.TextField(value = '5', input_filter=ft.NumbersOnlyInputFilter(),
                                        on_blur=self.done_minrating_clicked,
                                        autofocus=True, multiline=False, expand=True)
        
        self.button = ft.FilledButton("Create Blog", disabled=True,
                                      #on_click=self.go_button,
                                     height=50)

        self.tasks = ft.Column()

        self.width = 800
        self.autotranslated = True
        self.translatedtext = ""
        self.controls = [
            ft.Row(
                controls=[
                    self.titletext
                ],
            ),
            ft.Row(
                controls=[
                    self.blog_title,
                    ft.FloatingActionButton(
                        icon=ft.icons.CREATE, on_click=self.add_title_clicked
                    ),
                ],
            ),
            ft.Row(
                controls=[
                    self.blog_title_en,
                    ft.FloatingActionButton(
                        icon=ft.icons.UPDATE, on_click=self.update_title_clicked
                    ),
                    ft.FloatingActionButton(
                        icon=ft.icons.DONE, on_click=self.done_title_clicked
                    )
                ],
            ),
            ft.Row(
                controls=[
                    self.blog_message,
                    ft.FloatingActionButton(
                        icon=ft.icons.DONE, on_click=self.add_message_clicked
                    ),
                ],
            ),
            ft.Row(
                controls=[
                    self.blog_message_en,
                    ft.FloatingActionButton(
                        icon=ft.icons.UPDATE, on_click=self.update_message_clicked
                    ),
                    ft.FloatingActionButton(
                        icon=ft.icons.DONE, on_click=self.done_message_clicked
                    )
                ],
            ),
            ft.Row(
                controls=[
                    self.blog_species,
                    ft.FloatingActionButton(
                        icon=ft.icons.APPROVAL, on_click=self.validate_species
                    ),
                    ft.FloatingActionButton(
                        icon=ft.icons.DONE, on_click=self.done_species_clicked
                    ),
                ] 
            ),    
            ft.Row(
                controls=[
                    ft.Text('De minimale rating (tussen 1 en 125)          '),
                    self.blog_minrating,
                    ft.FloatingActionButton(
                        icon=ft.icons.NUMBERS, on_click=self.validate_species
                    ),
                    ft.FloatingActionButton(
                        icon=ft.icons.DONE, on_click=self.done_species_clicked
                    ),
                ] 
            ),             
            ft.Row(
                controls=[
                    self.button,
                ],
            ),

        ]

    # restart with edits of title
    def add_title_clicked(self, e):
        if self.blog_title_en.value == "" or not self.autotranslated:
            self.blog_title_en.value = "Suggestion: " + self.blog_title.value
        self.update()

    def add_lostfocus_clicked(self, e):
        if self.blog_title_en.value == "" or not self.autotranslated:
            self.blog_title_en.value = "Suggestion: " + self.blog_title.value
        self.blog_message.focus()
        self.update()

    # request changed proposed English title
    def update_title_clicked(self, e):
        self.translatedtext = self.blog_title_en.value
        self.blog_title_en.disabled = False        
        self.update()

    # done, move to next field
    def done_title_clicked(self, e):
        if self.blog_title_en.value != "Suggestion: " + self.blog_title.value:
            self.autotranslated = False
        self.blog_title.disabled = True
        self.blog_title_en.disabled = True
        self.blog_message.disabled = False       
        self.update()

    def add_message_clicked(self, e):
        if self.blog_message_en.value == "" or not self.autotranslated:
            self.blog_message_en.value = "Suggestion: " + self.blog_message.value
        self.blog_species.focus()
        self.update()

    # request changed proposed English title
    def update_message_clicked(self, e):
        self.translatedtext = self.blog_message_en.value
        self.blog_message_en.disabled = False        
        self.update()

    # done, move to next field
    def done_message_clicked(self, e):
        if self.blog_message_en.value != "Suggestion: " + self.blog_message.value:
            self.autotranslated = False
        self.blog_message_en.disabled = True
        self.update()

    def validate_species(self, e):
        # simply check if species spec can be found
        try:
            #     spobject = self.species_dict[self.blog_species]
            self.blog_species.color = 1/0 
            self.blog_species.color = ft.colors.GREEN    
        except:                       # not found in species (sp) database
            self.blog_species.color = ft.colors.RED
        self.update()

    def done_species_clicked(self, e):
        self.blog_minrating.focus()
        self.update()
  
    def count_selected_picture(self, e):
        # countBlogPictures(self, speciesname, minimumrating)
        self.update()

    def done_minrating_clicked(self, e):
        # Need to check if valid data given
        self.button.disabled = False
        self.button.focus()
        self.update()

    # button function to create input for blog 
    def on_click(self, e):
        # You can use these for that instead: page.window_close() or page.window_destroy)
        self.update()
        # self.window_close()

    def task_status_change(self):
        self.update()

    def task_delete(self, task):
        self.tasks.controls.remove(task)
        self.update()

    def before_update(self):
        # status = self.filter.tabs[self.filter.selected_index].text
        for task in self.tasks.controls:
             task.visible = True
        #         status == "all"
        #         or (status == "active" and task.completed == False)
        #         or (status == "completed" and task.completed)
        #    )

    def tabs_changed(self, e):
        self.update()


def main(page: ft.Page):

    page.title = "Make Blog"
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.update()

    # create application instance
    app = TodoApp()
    # add application's root control to the page
    page.add(app)


ft.app(target=main)
