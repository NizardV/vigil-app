using System.Security.Claims;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace DotnetProjects.Pages;

public class LoginModel(IConfiguration config) : PageModel
{
    [BindProperty] public string UserName { get; set; } = "";
    [BindProperty] public string Password { get; set; } = "";

    // Prototype : un seul compte lu dans la config. En vrai : ASP.NET Identity (hash, 2FA, verrouillage).
    public async Task<IActionResult> OnPostAsync()
    {
        if (UserName != config["Admin:User"] || Password != config["Admin:Password"] || string.IsNullOrEmpty(Password))
            return Page();

        var identity = new ClaimsIdentity([new Claim(ClaimTypes.Name, UserName)],
            CookieAuthenticationDefaults.AuthenticationScheme);
        await HttpContext.SignInAsync(new ClaimsPrincipal(identity));
        return RedirectToPage("/Projects");
    }
}
