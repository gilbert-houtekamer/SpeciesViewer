import flet as ft
import sview
import sview.model


class RepositoriesList(ft.Container):

    def __init__(self, page: ft.Page, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.page = page
        c = []
        for repo_path in sview.svconfig.repo_paths:
            c.append(RepositoryPane(sview.svconfig.repositories[repo_path]))
        self.content = ft.Column(controls=c)
        self.border = ft.border.all(1, ft.colors.BLACK)
        self.border_radius = ft.border_radius.all(10)
        self.bgcolor = ft.colors.GREEN_600
        self.padding = 10
        self.expand = True


    def new_repository(self, e):
        self.page.go("/repositories/new")


class RepositoryPane(ft.Container):

    def __init__(self, repo: sview.model.Repository, *args, **kwargs):
        self.repo = repo
        super().__init__(*args, **kwargs)
        self.content = ft.Row([
                ft.Container(
                    content=ft.Text(repo.name)
                )
            ])
        self.border=ft.border.all(1, ft.colors.BLACK38)
        self.border_radius=ft.border_radius.all(5)
        self.bgcolor=ft.colors.WHITE60
        self.padding=ft.padding.all(10)
        self.expand = True
        self.expand_loose = True
